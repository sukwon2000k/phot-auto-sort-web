import React, { useEffect, useMemo, useRef, useState } from "react";
import { apiClient } from "./api.js";
import AppShell from "./AppShell.jsx";
import { useRoute } from "./navigation.js";
import { formatBytes, selectFiles, selectionSummary } from "./files.js";

const PAGE_SIZE = 60;
const TERMINAL = new Set(["completed", "failed", "expired"]);
const STAGES = {
  queued: "분류 순서를 기다리고 있어요.",
  provisioning: "분류 서버를 준비하고 있어요. 처음에는 시간이 걸릴 수 있어요.",
  processing: "사진을 분류하고 있어요. 사진이 많으면 시간이 걸릴 수 있어요.",
  zipping: "결과를 ZIP으로 묶고 있어요.",
};

/** 현재 페이지 사진에만 미리보기 URL을 만들고 페이지를 벗어나면 해제합니다. */
function PreviewItem({ item, disabled, onRemove }) {
  const [url, setUrl] = useState("");
  useEffect(() => {
    if (item.isZip) return;
    const objectUrl = URL.createObjectURL(item.file);
    setUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [item]);
  return <li className="preview">
    {item.isZip ? <div className="archive-thumb" title="ZIP 내부 장수는 서버에서 확인합니다.">ZIP</div>
      : <img src={url || undefined} alt={item.file.name} loading="lazy" decoding="async" />}
    <button type="button" className="remove" disabled={disabled}
      aria-label={item.file.name + " 제거"} onClick={() => onRemove(item.id)}>×</button>
    <span className="name" title={item.file.name}>{item.file.name}</span>
    <span className="size">{formatBytes(item.file.size)}</span>
  </li>;
}

/** 컴포넌트 상태로 선택·업로드·비동기 작업 조회·ZIP 다운로드 흐름을 관리합니다. */
export default function App({ client = apiClient }) {
  const [route, navigate] = useRoute();
  const storageKey = "photo-sorter-last-job-" + client.mode;
  const [server, setServer] = useState(null);
  const [files, setFiles] = useState([]);
  const [page, setPage] = useState(0);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [phase, setPhase] = useState("idle");
  const [phaseText, setPhaseText] = useState("");
  const [dragging, setDragging] = useState(false);
  const [job, setJob] = useState(() => {
    try {
      const id = sessionStorage.getItem(storageKey);
      return id ? { id, status: "processing" } : null;
    } catch { return null; }
  });
  const [clock, setClock] = useState(Date.now());
  const inputRef = useRef(null);
  const submission = useRef(null);
  const busy = phase === "uploading" || Boolean(job && !TERMINAL.has(job.status));
  const summary = selectionSummary(files);
  const maxPage = Math.max(0, Math.ceil(files.length / PAGE_SIZE) - 1);
  const currentPage = Math.min(page, maxPage);
  const visible = files.slice(currentPage * PAGE_SIZE, (currentPage + 1) * PAGE_SIZE);
  const expires = Boolean(job?.expires_at && clock / 1000 >= job.expires_at);
  const downloadUrl = useMemo(() => {
    if (!job?.download_url) return "";
    try { return client.downloadUrl(job.download_url); } catch { return ""; }
  }, [client, job?.download_url]);

  useEffect(() => {
    const controller = new AbortController();
    let timer;
    const refresh = async () => {
      try {
        const status = await client.status(controller.signal);
        if (!controller.signal.aborted) setServer(status);
      } catch (reason) {
        if (reason.name !== "AbortError" && !controller.signal.aborted) setServer(null);
      }
      if (!controller.signal.aborted) timer = setTimeout(refresh, 5000);
    };
    refresh();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [client]);

  useEffect(() => {
    const timer = setInterval(() => setClock(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => () => submission.current?.abort(), []);

  useEffect(() => {
    // 영역 밖에 사진을 놓아도 브라우저가 이미지 페이지로 이동하지 않게 합니다.
    const preventNavigation = (event) => event.preventDefault();
    window.addEventListener("dragover", preventNavigation);
    window.addEventListener("drop", preventNavigation);
    return () => {
      window.removeEventListener("dragover", preventNavigation);
      window.removeEventListener("drop", preventNavigation);
    };
  }, []);

  useEffect(() => {
    if (!job?.id || TERMINAL.has(job.status)) return;
    const controller = new AbortController();
    let timer;
    const poll = async () => {
      try {
        const result = await client.job(job.id, controller.signal);
        if (controller.signal.aborted) return;
        if (TERMINAL.has(result.status)) {
          setJob(result);
          setPhase(result.status);
          setError(result.status === "failed" ? result.error || "분류에 실패했습니다." : "");
          return;
        }
        setJob(result);
        setPhaseText(STAGES[result.status] || "ZIP을 확인하고 사진을 분류해 결과를 만들고 있어요.");
      } catch (reason) {
        if (controller.signal.aborted) return;
        if ([404, 410].includes(reason.status)) {
          setJob((previous) => ({ ...previous, status: "expired" }));
          setPhase("failed");
          setError(reason.message);
          try { sessionStorage.removeItem(storageKey); } catch { /* 저장 차단은 분류에 영향이 없습니다. */ }
          return;
        }
        setPhaseText("연결을 확인하고 있어요. 작업 상태를 다시 조회할게요.");
      }
      if (!controller.signal.aborted) timer = setTimeout(poll, 2000);
    };
    poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [client, job?.id, job?.status, storageKey]);

  /** 선택과 드롭을 같은 규칙으로 검증하고 제외 이유는 최대 5개까지 표시합니다. */
  function addFiles(incoming) {
    if (busy || !server) return;
    const { accepted, rejected } = selectFiles(incoming, files, server.limits);
    setFiles((previous) => [...previous, ...accepted]);
    setNotice(rejected.length ? "추가하지 못한 파일: " + rejected.slice(0, 5).join(", ") +
      (rejected.length > 5 ? " 외 " + (rejected.length - 5) + "개" : "") : "");
  }

  /** 선택 항목 하나를 제거합니다. 파일 내용은 수정하지 않습니다. */
  function removeFile(id) {
    if (!busy) setFiles((previous) => previous.filter((item) => item.id !== id));
  }

  /** 업로드를 접수하고 작업 ID를 저장합니다. 모델의 모의 결과는 만들지 않습니다. */
  async function start() {
    if (busy || !server?.model_ready || server.busy || !files.length) return;
    setError("");
    setJob(null);
    try { sessionStorage.removeItem(storageKey); } catch { /* 임시 상태 저장이 없어도 사용할 수 있습니다. */ }
    navigate("/processing");
    setPhase("uploading");
    setPhaseText("사진·ZIP을 업로드하고 있어요.");
    const controller = new AbortController();
    submission.current = controller;
    try {
      const result = await client.submit(files.map((item) => item.file), {
        signal: controller.signal, timeoutSeconds: server.limits.upload_timeout_seconds,
        onPhase: (text) => { if (!controller.signal.aborted) setPhaseText(text); },
      });
      if (controller.signal.aborted) return;
      setJob(result);
      setPhase("processing");
      setPhaseText("업로드 완료 · 분류 작업 상태를 확인하고 있어요.");
      try { sessionStorage.setItem(storageKey, result.id); } catch { /* 재접속 복원만 생략합니다. */ }
    } catch (reason) {
      if (reason.name !== "AbortError") { setError(reason.message); setPhase("failed"); }
    }
  }

  useEffect(() => {
    if (job?.status === "completed") navigate("/result", true);
  }, [job?.id, job?.status, navigate]);

  const modelMessage = !server ? "서버 연결을 확인하고 있어요." :
    !server.model_ready ? server.message :
    server.busy && !busy ? "다른 사진을 정리하고 있어요. 잠시 후 다시 시도해 주세요." :
    "사진이나 ZIP을 선택하면 분류를 시작할 수 있어요.";

  return <AppShell route={route} navigate={navigate} server={server} files={files} summary={summary}
    visible={visible} currentPage={currentPage} maxPage={maxPage} setPage={setPage}
    busy={busy} phase={phase} phaseText={phaseText} job={job} notice={notice} error={error}
    dragging={dragging} setDragging={setDragging} inputRef={inputRef} addFiles={addFiles}
    clearFiles={() => { setFiles([]); setPage(0); setNotice(""); }} start={start}
    modelMessage={modelMessage} downloadUrl={downloadUrl} expires={expires} mode={client.mode}
    onNew={() => { setFiles([]); setPage(0); setNotice(""); setError(""); setPhase("idle"); navigate("/"); }}
    previewItems={visible.map((item) => <PreviewItem key={item.id} item={item} disabled={busy} onRemove={removeFile} />)} />;
}
