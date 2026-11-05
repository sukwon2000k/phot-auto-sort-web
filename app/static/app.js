/* 사진 선택·썸네일·업로드·작업 조회를 연결합니다. 브라우저에서 분류하지 않습니다. */
"use strict";
const $ = (id) => document.getElementById(id);
let selected = [];
let server = null;
let working = false;
let completedJob = null;
let pollingTimer = null;

/** 바이트 단위 크기를 사람이 읽기 쉬운 문자열로 반환합니다. */
function bytes(value) {
  if (value < 1024) return value + " B";
  if (value < 1024 ** 2) return (value / 1024).toFixed(1) + " KB";
  return (value / 1024 ** 2).toFixed(1) + " MB";
}

/** 상태 메시지를 안전한 텍스트로 표시합니다. */
function message(id, text) {
  $(id).textContent = text || "";
  $(id).hidden = !text;
}

/** 모델과 현재 작업 상태를 반영하여 조작 가능 여부를 갱신합니다. */
function controls() {
  $("start").disabled = working || !server?.model_ready || server?.busy || !selected.length;
  $("files").disabled = working || !server;
  $("clear").disabled = working;
  document.querySelectorAll(".remove").forEach((button) => { button.disabled = working; });
}

/** 선택 목록을 렌더링합니다. 사용자 파일명은 HTML로 해석하지 않습니다. */
function render() {
  const list = $("previews");
  list.replaceChildren();
  selected.forEach((item, index) => {
    const li = document.createElement("li");
    li.className = "preview";
    const img = document.createElement("img");
    img.src = item.url;
    img.loading = "lazy";
    img.decoding = "async";
    img.alt = item.file.name;
    img.onerror = () => { img.alt = "미리보기 불가"; };
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = item.file.name;
    name.title = item.file.name;
    const size = document.createElement("span");
    size.className = "size";
    size.textContent = bytes(item.file.size);
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "remove";
    remove.textContent = "×";
    remove.setAttribute("aria-label", item.file.name + " 제거");
    remove.onclick = () => {
      if (working) return;
      URL.revokeObjectURL(item.url);
      selected.splice(index, 1);
      render();
    };
    li.append(img, remove, name, size);
    list.append(li);
  });
  $("summary").textContent = selected.length + "장 · " + bytes(selected.reduce((n, x) => n + x.file.size, 0));
  $("selection-tools").hidden = !selected.length;
  controls();
}

/** 파일 선택과 드롭을 같은 방식으로 추가하고 서버 제한에 맞지 않는 파일을 안내합니다. */
function addFiles(files) {
  if (working || !server) return;
  const rejected = [];
  let total = selected.reduce((n, x) => n + x.file.size, 0);
  for (const file of files) {
    const ext = "." + file.name.split(".").pop().toLowerCase();
    let reason = "";
    if (!server.limits.extensions.includes(ext)) reason = "지원하지 않는 형식";
    else if (file.size > server.limits.max_file_bytes) reason = "파일 용량 초과";
    else if (selected.length >= server.limits.max_files) reason = "파일 수 초과";
    else if (total + file.size > server.limits.max_request_bytes) reason = "전체 용량 초과";
    if (reason) rejected.push(file.name + " (" + reason + ")");
    else {
      selected.push({ file, url: URL.createObjectURL(file) });
      total += file.size;
    }
  }
  message("selection-message", rejected.length ? "추가하지 못한 파일: " + rejected.join(", ") : "");
  render();
}

/** 서버 준비 상태와 제한을 조회합니다. 실패하면 분류 버튼을 닫고 다음 주기에 다시 조회합니다. */
async function refreshStatus() {
  try {
    const response = await fetch("/api/status", { cache: "no-store" });
    if (!response.ok) throw new Error();
    server = await response.json();
    const limits = server.limits;
    $("limits").textContent = "JPG · JPEG · JFIF · PNG · WEBP / 최대 " + limits.max_files +
      "장 / 파일당 " + bytes(limits.max_file_bytes) + " / 전체 요청 " + bytes(limits.max_request_bytes) +
      " (전송 부가 데이터 포함) / 사진당 " + limits.max_pixels.toLocaleString() + "픽셀";
    $("retention").textContent = "원본 업로드는 처리 후 삭제해요. 결과 ZIP은 완료 후 " +
      Math.round(limits.retention_seconds / 60) + "분 동안 다운로드할 수 있고 이후 자동 삭제돼요. 서버가 재시작되면 결과도 삭제돼요.";
    $("model-status").textContent = !server.model_ready ? server.message :
      server.busy && !working ? "다른 사진을 정리하고 있어요. 잠시 후 다시 시도해 주세요." : "사진을 선택하면 분류를 시작할 수 있어요.";
  } catch {
    server = null;
    $("model-status").textContent = "서버에 연결하지 못했어요. 연결 상태를 다시 확인하고 있어요.";
  }
  controls();
}

/** multipart 업로드의 실제 종료 이벤트를 이용해 업로드와 분류 단계를 구분합니다. */
function sendUpload(data) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/jobs");
    xhr.timeout = 180000;
    xhr.upload.onload = () => { $("process-text").textContent = "업로드 완료 · 서버에서 사진을 접수하고 있어요."; };
    xhr.onload = () => {
      let result;
      try { result = JSON.parse(xhr.responseText); } catch { reject(new Error("서버 응답을 읽지 못했어요.")); return; }
      if (xhr.status >= 200 && xhr.status < 300) resolve(result);
      else reject(new Error(typeof result.detail === "string" ? result.detail : "업로드에 실패했어요."));
    };
    xhr.onerror = () => reject(new Error("업로드 연결이 끊겼어요. 서버에서 처리가 계속될 수 있어요."));
    xhr.ontimeout = () => reject(new Error("응답 대기 시간이 끝났어요. 서버 상태를 확인한 후 다시 시도해 주세요."));
    xhr.send(data);
  });
}

/** 작업 상태를 조회하고 완료 시 개수와 만료 시간이 있는 다운로드 링크를 표시합니다. */
async function pollJob(id) {
  try {
    const response = await fetch("/api/jobs/" + id, { cache: "no-store" });
    const job = await response.json();
    if (!response.ok) throw Object.assign(new Error(job.detail || "상태 확인 실패"), { terminal: true });
    if (job.status === "processing") {
      $("process-text").textContent = "사진을 분류하고 ZIP을 만들고 있어요. 잠시만 기다려 주세요.";
    } else {
      working = false;
      $("process").hidden = true;
      if (job.status === "failed") message("error", job.error);
      else {
        completedJob = job;
        for (const key of ["person", "food", "landscape"]) $("count-" + key).textContent = job.counts[key];
        $("count-failed").textContent = job.failed_count;
        $("result-title").textContent = Object.values(job.counts).reduce((a, b) => a + b, 0) ?
          "사진이 제자리를 찾았어요." : "분류할 수 있는 사진이 없었어요.";
        $("result-description").textContent = "ZIP에는 세 폴더와 classification_report.csv가 들어 있어요. 실패한 사진과 이유는 보고서에서 확인하세요.";
        $("download").href = job.download_url;
        $("download").removeAttribute("aria-disabled");
        $("result").hidden = false;
        updateExpiry();
        $("result").scrollIntoView({ behavior: "smooth", block: "nearest" });
      }
      await refreshStatus();
      return;
    }
  } catch (error) {
    if (error.terminal) {
      working = false;
      $("process").hidden = true;
      message("error", error.message);
      await refreshStatus();
      return;
    }
    $("process-text").textContent = "연결을 확인하고 있어요. 작업 상태를 다시 조회할게요.";
  }
  pollingTimer = setTimeout(() => pollJob(id), 1000);
}

/** 만료된 링크를 비활성화하며 새 작업 전까지 기존 결과를 유지합니다. */
function updateExpiry() {
  if (!completedJob) return;
  if (Date.now() / 1000 >= completedJob.expires_at) {
    $("download").removeAttribute("href");
    $("download").setAttribute("aria-disabled", "true");
    $("expiry").textContent = "보관 기간이 끝났어요. 사진을 다시 업로드해 주세요.";
  } else {
    $("expiry").textContent = new Date(completedJob.expires_at * 1000).toLocaleString("ko-KR") + "까지 다운로드 가능";
  }
}

$("files").onchange = (event) => { addFiles(event.target.files); event.target.value = ""; };
$("clear").onclick = () => {
  if (working) return;
  selected.forEach((item) => URL.revokeObjectURL(item.url));
  selected = [];
  message("selection-message", "");
  render();
};
for (const event of ["dragenter", "dragover"]) $("dropzone").addEventListener(event, (e) => {
  e.preventDefault();
  if (!working) $("dropzone").classList.add("dragging");
});
for (const event of ["dragleave", "drop"]) $("dropzone").addEventListener(event, (e) => {
  e.preventDefault();
  $("dropzone").classList.remove("dragging");
});
$("dropzone").addEventListener("drop", (e) => addFiles(e.dataTransfer.files));
// 드롭 영역 바깥에 파일을 놓아 브라우저가 사진 페이지로 이동하는 일을 방지합니다.
window.addEventListener("dragover", (e) => e.preventDefault());
window.addEventListener("drop", (e) => e.preventDefault());
$("start").onclick = async () => {
  if (working || !selected.length || !server?.model_ready || server?.busy) return;
  working = true;
  completedJob = null;
  clearTimeout(pollingTimer);
  $("result").hidden = true;
  message("error", "");
  $("process").hidden = false;
  $("process-text").textContent = "사진을 업로드하고 있어요.";
  controls();
  const data = new FormData();
  selected.forEach((item) => data.append("files", item.file));
  try {
    const job = await sendUpload(data);
    await pollJob(job.id);
  } catch (error) {
    working = false;
    $("process").hidden = true;
    message("error", error.message);
    await refreshStatus();
  }
};
refreshStatus();
setInterval(refreshStatus, 5000);
setInterval(updateExpiry, 1000);
window.addEventListener("beforeunload", () => selected.forEach((item) => URL.revokeObjectURL(item.url)));
