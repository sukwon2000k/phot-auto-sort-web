/**
 * 화면과 서버 호출을 분리합니다.
 * local은 현재 FastAPI, cloud는 추후 S3 직접 업로드 API 계약을 사용합니다.
 * cloud 어댑터만으로 AWS 자원이 만들어지거나 GPU가 실행되는 것은 아닙니다.
 */

/** JSON API를 호출하고 서버 오류를 사용자에게 전달할 Error로 변환합니다. */
async function requestJson(url, options = {}) {
  let response;
  try {
    response = await fetch(url, { cache: "no-store", ...options });
  } catch (error) {
    if (error.name === "AbortError") throw error;
    throw new Error("서버 연결을 확인해 주세요.");
  }
  let body;
  try { body = await response.json(); }
  catch { throw new Error("서버 응답을 읽지 못했습니다."); }
  if (!response.ok) {
    const error = new Error(typeof body.detail === "string" ? body.detail : "요청을 처리하지 못했습니다.");
    error.status = response.status;
    throw error;
  }
  return body;
}

/** 업로드 완료 이벤트를 전달합니다. 분류 진행률로 업로드 바이트를 대신 표시하지 않습니다. */
function sendFile({ method, url, body, headers = {}, timeout, signal, onUploaded, onProgress, expectJson }) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    let settled = false;
    const abort = () => xhr.abort();
    const finish = (error, value) => {
      if (settled) return;
      settled = true;
      signal?.removeEventListener("abort", abort);
      if (error) reject(error); else resolve(value);
    };
    xhr.open(method, url);
    xhr.timeout = timeout;
    Object.entries(headers).forEach(([name, value]) => xhr.setRequestHeader(name, value));
    xhr.upload.onprogress = (event) => onProgress?.(event.loaded);
    xhr.upload.onload = () => onUploaded?.();
    xhr.onload = () => {
      let result = null;
      if (expectJson) {
        try { result = JSON.parse(xhr.responseText); }
        catch { finish(new Error("서버 응답을 읽지 못했습니다.")); return; }
      }
      if (xhr.status >= 200 && xhr.status < 300) finish(null, result);
      else { const error = new Error(result?.detail || "업로드하지 못했습니다. 용량과 연결 상태를 확인해 주세요."); error.status = xhr.status; finish(error); }
    };
    xhr.onerror = () => finish(new Error("업로드 연결이 끊겼습니다. 서버에서 처리가 계속될 수 있습니다."));
    xhr.ontimeout = () => finish(new Error("업로드 응답 시간이 초과됐습니다. 서버 상태를 확인해 주세요."));
    xhr.onabort = () => finish(new DOMException("화면이 닫혀 업로드 연결을 종료했습니다.", "AbortError"));
    signal?.addEventListener("abort", abort, { once: true });
    if (signal?.aborted) { finish(new DOMException("취소됨", "AbortError")); return; }
    xhr.send(body);
  });
}

/** 배포 환경에 맞는 API 클라이언트를 만듭니다. 비밀 AWS 키는 브라우저에 넣지 않습니다. */
export function createApi({ baseUrl = "", mode = "local" } = {}) {
  if (!["local", "cloud"].includes(mode)) throw new Error("VITE_API_MODE는 local 또는 cloud여야 합니다.");
  const base = baseUrl.replace(/\/$/, "");
  const api = (path) => base + path;
  let pending = null; // 같은 화면에서 재시도하면 완료 조각을 다시 보내지 않습니다.
  return {
    mode,
    canResume: () => Boolean(pending),
    items: (id, category, page, signal) => requestJson(api(`/api/jobs/${id}/items?category=${category}&page=${page}`), { signal }),
    export: (id, selection, signal) => requestJson(api(`/api/jobs/${id}/exports`), { method: "POST", signal, headers: { "Content-Type": "application/json" }, body: JSON.stringify(selection) }),
    exportStatus: (id, exportId, signal) => requestJson(api(`/api/jobs/${id}/exports/${exportId}`), { signal }),
    status: (signal) => requestJson(api("/api/status"), { signal }),
    job: (id, signal) => requestJson(api("/api/jobs/" + encodeURIComponent(id)), { signal }),
    downloadUrl: (url) => {
      if (url.startsWith("/") && !url.startsWith("//")) return base + url;
      if (url.startsWith("https://")) return url;
      throw new Error("안전하지 않은 다운로드 주소입니다.");
    },
    async submit(files, { signal, timeoutSeconds = 1800, onPhase = () => {}, onProgress = () => {} } = {}) {
      const timeout = (timeoutSeconds + 60) * 1000;
      if (mode === "local") {
        const total = files.reduce((sum, file) => sum + file.size, 0);
        const descriptors = [];
        // 내용을 재압축하지 않고 파일 명세만 준비합니다. 큰 원본 전체를 메모리로 읽지 않습니다.
        for (let index = 0; index < files.length; index++) {
          const file = files[index];
          descriptors.push({ name: file.name, size: file.size });
          if (index % 100 === 0 || index === files.length - 1) {
            onProgress({ phase: "preparing", processed: index + 1, total: files.length,
              percent: Math.round((index + 1) * 100 / files.length) });
            await new Promise(resolve => setTimeout(resolve, 0));
          }
        }
        const signature = JSON.stringify(files.map(f => [f.webkitRelativePath || f.name, f.size, f.lastModified]));
        if (pending && pending.signature !== signature) {
          await requestJson(api(`/api/uploads/${pending.id}`), { method: "DELETE", signal });
          pending = null;
        }
        if (!pending) {
          const session = await requestJson(api("/api/uploads"), {
            method: "POST", signal, headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ files: descriptors }),
          });
          if (!session.id || !Number.isInteger(session.chunk_bytes) || session.chunk_bytes <= 0 || session.chunk_bytes >= 100000000) {
            throw new Error("업로드 조각 설정이 잘못됐습니다.");
          }
          pending = { ...session, signature, done: new Set() };
        }
        let acknowledged = 0;
        for (let i = 0; i < files.length; i++) {
          const file = files[i];
          for (let start = 0, part = 0; start < file.size; start += pending.chunk_bytes, part++) {
            const blob = file.slice(start, Math.min(file.size, start + pending.chunk_bytes));
            const key = `${i}:${part}`;
            if (!pending.done.has(key)) {
              for (let attempt = 0; ; attempt++) {
                if (signal?.aborted) throw new DOMException("취소됨", "AbortError");
                onPhase(`사진·ZIP 업로드 중 · 파일 ${i + 1}/${files.length}${attempt ? " · 연결 재시도 " + attempt : ""}`);
                try {
                  await sendFile({ method: "PUT", url: api(`/api/uploads/${pending.id}/${i}/${part}`),
                    body: blob, headers: { "Content-Type": "application/octet-stream" }, timeout, signal, expectJson: true,
                    onProgress: (loaded) => onProgress({ phase: "uploading", processed: acknowledged + Math.min(blob.size, loaded), total,
                      percent: Math.min(99.9, (acknowledged + Math.min(blob.size, loaded)) * 100 / total) }),
                  });
                  pending.done.add(key);
                  break;
                } catch (error) {
                  if ([404, 410].includes(error.status)) pending = null;
                  if (signal?.aborted || error.name === "AbortError" || attempt >= 3 ||
                    (error.status >= 400 && error.status < 500 && ![408, 429].includes(error.status))) throw error;
                  await new Promise(resolve => setTimeout(resolve, (attempt + 1) * 1000));
                }
              }
            }
            acknowledged += blob.size;
            onProgress({ phase: "uploading", processed: acknowledged, total, percent: acknowledged * 100 / total });
          }
        }
        onPhase("업로드 완료 · 서버에서 파일을 합칠 준비를 하고 있어요.");
        try {
          const result = await requestJson(api(`/api/uploads/${pending.id}/complete`), { method: "POST", signal });
          pending = null;
          if (result.status === "failed") throw new Error(result.error || "분류에 실패했습니다.");
          return result;
        } catch (error) {
          if ([404, 410].includes(error.status)) pending = null;
          throw error;
        }
      }
      // AWS에서는 큰 사진 파일을 Lambda/API Gateway로 통과시키지 않고 S3로 직접 보냅니다.
      const session = await requestJson(api("/api/upload-sessions"), {
        method: "POST", signal, headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ files: files.map((file) => ({
          name: file.name, size: file.size, content_type: file.type || "application/octet-stream",
        })) }),
      });
      if (!session.id || !Array.isArray(session.uploads) || session.uploads.length !== files.length) {
        throw new Error("업로드 준비 응답이 올바르지 않습니다.");
      }
      for (const [index, file] of files.entries()) {
        const upload = session.uploads[index];
        if (!upload.url?.startsWith("https://") || upload.method !== "PUT") {
          throw new Error("올바른 S3 업로드 주소가 필요합니다.");
        }
        onPhase("사진·ZIP을 업로드하고 있어요. 파일 " + (index + 1) + " / " + files.length);
        await sendFile({
          method: "PUT", url: upload.url, headers: upload.headers || {}, body: file,
          timeout, signal, expectJson: false,
        });
      }
      onPhase("업로드 완료 · 분류 작업을 접수하고 있어요.");
      return requestJson(api("/api/jobs"), {
        method: "POST", signal, headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: session.id }),
      });
    },
  };
}

export const apiClient = createApi({
  baseUrl: import.meta.env.VITE_API_BASE_URL || "",
  mode: import.meta.env.VITE_API_MODE || "local",
});
