import React, { useEffect, useRef } from "react";
import { formatBytes } from "./files.js";
import ResultReview from "./ResultReview.jsx";

/** 사진·업로드·폴더 의미를 나타내는 작은 SVG 아이콘입니다. */
export function Icon({ name = "folder", className = "" }) {
  const paths = {
    folder: <path d="M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z" />,
    upload: <><path d="M12 16V3m-5 5 5-5 5 5M4 15v4a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-4" /></>,
    landscape: <><rect x="3" y="3" width="18" height="18" rx="3" /><path d="m3 17 6-7 4 5 3-3 5 6" /><circle cx="16" cy="8" r="1" /></>,
    person: <><circle cx="12" cy="8" r="4" /><path d="M4 21v-2a8 8 0 0 1 16 0v2" /></>,
    food: <><path d="M4 3v6a3 3 0 0 0 6 0V3M7 3v18M20 21V3c-4 0-5 4-5 8h5" /></>,
    check: <path d="m5 12 4 4L19 6" />,
    arrow: <path d="M4 12h16m-6-6 6 6-6 6" />,
    info: <><circle cx="12" cy="12" r="9" /><path d="M12 11v6M12 7h.01" /></>,
  };
  return <svg className={className} width="24" height="24" viewBox="0 0 24 24"
    fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name] || paths.folder}</svg>;
}

/** 서버 제한의 핵심은 선택 화면에, 세부 설명은 사용방법 페이지에 표시합니다. */
function Limits({ limits, expanded = false }) {
  if (!limits) return <p className="hint">업로드 제한을 확인하고 있습니다.</p>;
  return <div className={"limit-info" + (expanded ? " expanded" : "")}>
    <span>JPG · JPEG · JFIF · PNG · WEBP · ZIP</span>
    <span>사진 최대 <b>{limits.max_files.toLocaleString()}장</b> · 전체 <b>{formatBytes(limits.max_request_bytes)}</b></span>
    <span>사진당 {formatBytes(limits.max_file_bytes)} · ZIP당 {formatBytes(limits.max_archive_bytes)}</span>
    {expanded && <><span>압축 해제 후 합계 {formatBytes(limits.max_expanded_bytes)} · 사진당 {limits.max_pixels.toLocaleString()}픽셀</span>
      <p>표시된 전체 한도 안에서 자동 분할 업로드합니다. ZIP 내부 사진 수는 서버에서 확인하며, 큰 ZIP은 자동으로 작은 조각으로 전송합니다. ZIP64를 지원하며 암호·다중 볼륨·중첩 ZIP은 지원하지 않습니다.</p></>}
  </div>;
}

/** 실제 처리 완료 수로만 게이지를 그립니다. 전체 수를 모르는 단계는 무한 로딩으로 표시합니다. */
export function ProgressView({ job, phase, phaseText, error, onBack, uploadProgress }) {
  if (!job && phase !== "uploading" && !error) {
    return <section className="empty-screen"><Icon name="folder" /><h1>진행 중인 작업이 없어요.</h1>
      <button className="primary" onClick={onBack}>사진 정리하러 가기 <Icon name="arrow" /></button></section>;
  }
  const progress = phase === "uploading" ? uploadProgress : job?.progress;
  const measured = typeof progress?.percent === "number" && typeof progress?.total === "number";
  const percent = measured ? Math.max(0, Math.min(100, progress.percent)) : null;
  const stage = progress?.phase;
  const title = error ? "잠깐, 확인이 필요해요." : phase === "uploading" ? (stage === "preparing" ? "업로드할 파일을 준비해요." : "사진을 나눠서 옮기고 있어요.") :
    stage === "merging" ? "받은 파일을 합치고 있어요." :
    stage === "zipping" ? "분류 완료! ZIP을 준비해요." :
    stage === "validating" ? "사진을 확인하고 있어요." :
    stage === "extracting" ? "ZIP 속 사진을 꺼내고 있어요." : "사진이 제자리를 찾는 중.";
  return <section className="progress-screen" aria-labelledby="progress-title">
    <div className={"progress-orbit" + (error ? " paused" : "")}><Icon name={error ? "info" : "folder"} /></div>
    <p className="eyebrow">02 / 사진 정리</p><h1 id="progress-title">{title}</h1>
    <p className="screen-description">{error || phaseText || "서버에서 작업 상태를 확인하고 있어요."}</p>
    {!error && <div className="gauge-panel">
      <div className="gauge-label"><span>{stage === "uploading" ? "업로드" : stage === "preparing" ? "파일 준비" : measured ? "사진·파일 처리" : "작업 준비 중"}</span>
        <strong>{measured ? Math.floor(percent) + "%" : "잠시만요"}</strong></div>
      <div className={"gauge-track" + (!measured ? " indeterminate" : "")} role="progressbar"
        aria-label="사진 분류 진행률" aria-valuemin={0} aria-valuemax={100}
        aria-valuenow={measured ? percent : undefined} aria-valuetext={measured ?
          progress.processed + "개 / " + progress.total + "개 처리, " + percent + "%" : "전체 수를 확인하고 있습니다."}>
        <span style={measured ? { width: percent + "%" } : undefined} />
      </div>
      <p className="gauge-detail">{measured ?
        <>{["uploading", "merging", "extracting", "zipping"].includes(stage) ? `${formatBytes(progress.processed)} / ${formatBytes(progress.total)}` : <>전체 <b>{progress.total.toLocaleString()}개</b> 중 <b>{progress.processed.toLocaleString()}개</b> 처리</>}</> :
        "전체 대상 수가 확인되고 사진 처리가 시작되면 진행률을 표시해요."}</p>
      {stage === "zipping" && <p className="hint">사진 처리는 100% 끝났어요. ZIP 저장이 끝나면 결과 화면으로 넘어가요.</p>}
    </div>}
    <div className="mini-folders" aria-hidden="true">{["landscape", "person", "food"].map((type) =>
      <span className={"mini-folder " + type} key={type}><Icon name={type} /></span>)}</div>
    {error ? <><p className="error" role="alert">{error}</p><button className="primary" onClick={onBack}>사진 선택으로 돌아가기 <Icon name="arrow" /></button></> :
      <p className="hint">실제로 처리한 파일 수를 기준으로 표시해요. 실패한 파일도 처리 수에 포함돼요.</p>}
  </section>;
}

/** 사용법·분류 기준·제한·보관 정책을 별도 URL에서 제공합니다. */
function GuideView({ server, mode, onBack }) {
  return <article className="guide-page"><p className="eyebrow">HOW TO USE</p><h1>사진 정리함, 이렇게 써요.</h1>
    <p className="screen-description">사진 선택부터 다운로드까지, 세 단계면 충분해요.</p>
    <ol className="guide-steps">{[
      ["01", "사진이나 ZIP을 올려주세요.", "여러 사진을 선택하거나 드래그해서 올리세요. 사진이 많으면 일반 ZIP으로 묶어도 됩니다. ZIP 안 하위 폴더의 사진도 함께 처리해요."],
      ["02", "분류 시작을 누르고 기다려요.", "압축 해제와 사진 확인 뒤 실제 처리한 개수와 진행률을 표시해요. 손상된 사진은 실패로 기록하고 나머지 사진을 계속 정리합니다."],
      ["03", "결과를 확인하고 ZIP을 받아요.", "풍경 및 기타, 인물, 음식 카드를 누르면 사진을 확인하고 선택할 수 있어요. 선택한 사진은 ZIP 하나로 저장하며, 원하면 전체 결과도 받을 수 있어요."],
    ].map(([number, title, description]) => <li key={number}><span>{number}</span><div><h2>{title}</h2><p>{description}</p></div></li>)}</ol>
    <section className="guide-section"><h2>세 폴더의 기준</h2><div className="guide-categories">
      <p><strong className="category-dot landscape">풍경 및 기타</strong>사람이나 음식이 주요 대상이 아닌 나머지 사진</p>
      <p><strong className="category-dot person">인물</strong>사람이 주요 대상인 사진</p>
      <p><strong className="category-dot food">음식</strong>음식이 주요 대상인 사진</p></div>
      <p>모델이 분류한 결과를 그대로 사용해요. 경계가 모호한 사진은 예상과 다르게 분류될 수 있어요.</p></section>
    <section className="guide-section"><h2>지원 형식과 업로드 제한</h2><Limits limits={server?.limits} expanded /></section>
    <section className="guide-section"><h2>원본과 보관</h2>
      <p>사진은 다시 인코딩하지 않고 원본 바이트 그대로 복사해요. 이름이 겹치면 _2, _3 등을 붙이고, 안전하지 않은 파일명은 정제해요.</p>
      <p>검토용 원본 사본과 결과 ZIP은 완료 후 {server ? Math.round(server.limits.retention_seconds / 60) + "분" : "정해진 시간"} 동안 다운로드할 수 있으며 이후 자동 정리합니다.
        {mode === "local" ? " 로컬 서버가 재시작되면 기존 결과도 삭제돼요." : " 분류 서버를 준비할 때 대기 시간이 생길 수 있어요."}</p>
      <p>업로드 후 접수한 작업은 같은 탭에서 다시 조회할 수 있어요. 원본 사진은 브라우저 저장소에 보관하지 않습니다.</p></section>
    <button className="primary" onClick={onBack}>사진 정리하러 가기 <Icon name="arrow" /></button>
  </article>;
}

/** 기능 우선 홈과 사용법·진행·완료 화면의 공통 레이아웃입니다. */
export default function AppShell(props) {
  const { client, uploadProgress, folderInputRef, canResume, route, navigate, server, files, summary, visible, currentPage, maxPage, setPage,
    busy, phase, phaseText, job, notice, error, dragging, setDragging, inputRef,
    addFiles, clearFiles, start, modelMessage, previewItems, downloadUrl, expires, onNew, mode } = props;
  const pageRef = useRef(null);
  useEffect(() => { pageRef.current?.focus({ preventScroll: true }); }, [route]);
  const isHome = !["/guide", "/processing", "/result"].includes(route);
  const link = (event, path) => { event.preventDefault(); navigate(path); };
  return <div className="site-shell">
    <header className="topbar"><a className="brand" href="/" onClick={(event) => link(event, "/")}>
      <span className="brand-symbol"><Icon name="folder" /></span><span>사진 정리함<small>PHOTO AUTO SORTER</small></span></a>
      <nav aria-label="메인 메뉴">{busy && <a className="running-link" href="/processing" onClick={(event) => link(event, "/processing")}><span />분류 현황</a>}
        <a className={"guide-link" + (route === "/guide" ? " active" : "")} href="/guide" onClick={(event) => link(event, "/guide")}><Icon name="info" /> 사용방법</a></nav>
    </header>
    <main ref={pageRef} tabIndex={-1}>
      {route === "/guide" && <GuideView server={server} mode={mode} onBack={() => navigate("/")} />}
      {route === "/processing" && <ProgressView uploadProgress={uploadProgress} job={job} phase={phase} phaseText={phaseText} error={error} onBack={() => navigate("/")} />}
      {route === "/result" && <ResultReview client={client} job={job} downloadUrl={downloadUrl} expires={expires} onBack={onNew} />}
      {isHome && <>
        <div className="home-heading"><div><p className="eyebrow">사진은 남기고, 정리는 가볍게</p><h1>사진 정리, <span>여기서 끝.</span></h1></div>
          <span className="original-badge"><Icon name="check" /> 원본 그대로</span></div>
        {job?.status === "completed" && <div className="previous-result"><span>정리한 사진의 ZIP이 준비돼 있어요.</span>
          <a href="/result" onClick={(event) => link(event, "/result")}>결과 다시 보기 <Icon name="arrow" /></a></div>}
        <div className="home-grid"><section className="workspace" aria-labelledby="upload-title" aria-busy={busy}>
          <div className="section-head"><h2 id="upload-title">정리할 사진을 올려주세요.</h2><span className="step-badge">01 / 시작</span></div>
          <div className={"dropzone" + (dragging ? " dragging" : "")}
            onDragOver={(event) => { event.preventDefault(); if (!busy) setDragging(true); }}
            onDragLeave={() => setDragging(false)}
            onDrop={(event) => { event.preventDefault(); setDragging(false); addFiles(Array.from(event.dataTransfer.files)); }}>
            <div className="upload-art" aria-hidden="true"><span className="art-photo back"><Icon name="landscape" /></span><span className="art-photo front"><Icon name="upload" /></span><span className="art-spark">+</span></div>
            <h3>사진이나 ZIP을 여기에 놓아주세요</h3><p>한 장도, 몇천 장도 한 번에.</p>
            <button type="button" className="choose" disabled={busy || !server} onClick={() => inputRef.current?.click()}>사진·ZIP 선택 <span aria-hidden="true">＋</span></button>
            <button type="button" className="choose" disabled={busy || !server} onClick={() => folderInputRef.current?.click()}>폴더 선택</button>
            <input ref={folderInputRef} className="file-input" type="file" multiple webkitdirectory="" directory="" aria-label="사진 폴더 선택" disabled={busy || !server}
              onChange={event => { addFiles(Array.from(event.target.files)); event.target.value = ""; }} />
            <input ref={inputRef} className="file-input" type="file" multiple accept=".jpg,.jpeg,.jfif,.png,.webp,.zip"
              aria-label="사진 및 ZIP 파일" disabled={busy || !server}
              onChange={(event) => { addFiles(Array.from(event.target.files)); event.target.value = ""; }} />
          </div>
          <Limits limits={server?.limits} />
          {notice && <p className="notice" role="status">{notice}</p>}
          {!!files.length && <><div className="selection-tools"><span>사진 <b>{summary.photos}장</b> · ZIP <b>{summary.archives}개</b> · {formatBytes(summary.bytes)}</span>
            <button type="button" className="text-button" disabled={busy} onClick={clearFiles}>전체 초기화</button></div>
            <ul className="previews" aria-label="선택한 사진">{previewItems}</ul></>}
          {files.length > 60 && <div className="pagination"><button disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>이전</button>
            <span>{currentPage + 1} / {maxPage + 1}</span><button disabled={currentPage === maxPage} onClick={() => setPage(currentPage + 1)}>다음</button></div>}
          <div className="action-row"><p role="status"><span className={"status-dot" + (server?.model_ready ? " ready" : "")} />{modelMessage}</p>
            <button type="button" className="primary" disabled={busy || !server?.model_ready || (server?.busy && !canResume) || !files.length} onClick={start}>{canResume ? "업로드 이어서 재시도" : "분류 시작"} <Icon name="arrow" /></button></div>
          {error && <p className="error" role="alert">{error}</p>}
        </section>
        <aside className="folder-aside" aria-label="분류 결과 폴더"><div className="aside-heading"><span className="tiny-line" /><p>섞여 있던 사진이<br /><b>세 폴더로 착착.</b></p></div>
          <div className="folder-stack">{[["landscape", "풍경 및 기타"], ["person", "인물"], ["food", "음식"]].map(([key, label]) =>
            <div className={"folder-preview " + key} key={key}><div className="folder-tab" /><Icon name={key} /><div><b>{label}</b><span>{key}</span></div><Icon name="check" /></div>)}</div>
          <div className="aside-bottom"><span>원본 보존</span><span>ZIP으로 한 번에</span></div></aside>
        </div>
      </>}
    </main>
    <footer><span>사진 정리함 <small>내 사진을 정리하는 작은 도구</small></span><a href="/guide" onClick={(event) => link(event, "/guide")}>사용방법과 보관 안내</a></footer>
  </div>;
}
