import React, { useEffect, useRef, useState } from "react";
import { formatBytes } from "./files.js";

const categories = [["landscape", "풍경 및 기타"], ["person", "인물"], ["food", "음식"]];
const all = () => Object.fromEntries(categories.map(([name]) => [name, true]));

/** 분류별 검토·페이지 이동·사진 선택과 서버 ZIP 생성 상태를 관리합니다. */
export default function ResultReview({ job, client, expires, downloadUrl, onBack }) {
  const [category, setCategory] = useState(null);
  const [page, setPage] = useState(0);
  const [listing, setListing] = useState(null);
  const [defaults, setDefaults] = useState(all);
  const [overrides, setOverrides] = useState(new Map());
  const [error, setError] = useState("");
  const [exporting, setExporting] = useState(false);
  const [exportResult, setExportResult] = useState(null);
  const controller = useRef(null);
  const dialogRef = useRef(null);
  const returnFocus = useRef(null);
  const downloadRef = useRef(null);
  useEffect(() => () => controller.current?.abort(), []);
  useEffect(() => {
    if (!category || !job?.id) return;
    const abort = new AbortController();
    setListing(null); setError("");
    client.items(job.id, category, page, abort.signal).then(result => {
      if (!abort.signal.aborted) setListing(result);
    }).catch(reason => { if (!abort.signal.aborted) setError(reason.message); });
    return () => abort.abort();
  }, [job?.id, category, page, client]);
  useEffect(() => {
    const dialog = dialogRef.current;
    if (category && dialog && !dialog.open) dialog.showModal();
    if (!category && dialog?.open) dialog.close();
  }, [category]);

  if (job?.status !== "completed") return <section className="empty-screen"><h1>아직 정리된 사진이 없어요.</h1><button onClick={onBack}>사진 선택으로 돌아가기</button></section>;
  const total = Object.values(job.counts || {}).reduce((sum, count) => sum + count, 0);
  let selectedCount = categories.reduce((sum, [key]) => sum + (defaults[key] ? job.counts?.[key] || 0 : 0), 0);
  for (const value of overrides.values()) selectedCount += Number(value.selected) - Number(defaults[value.category]);
  const selected = item => overrides.get(item.id)?.selected ?? defaults[item.class_name];

  /** 분류 전체 선택 상태를 바꾸면 해당 분류의 개별 예외만 초기화합니다. */
  function selectCategory(value) {
    setDefaults(previous => ({ ...previous, [category]: value }));
    setOverrides(previous => new Map([...previous].filter(([, item]) => item.category !== category)));
    setExportResult(null);
  }
  /** 다른 분류와 페이지에서도 선택을 유지합니다. 사진 자체는 이동하거나 수정하지 않습니다. */
  function toggle(item) {
    setOverrides(previous => {
      const next = new Map(previous);
      const value = !(previous.get(item.id)?.selected ?? defaults[item.class_name]);
      if (value === defaults[item.class_name]) next.delete(item.id);
      else next.set(item.id, { category: item.class_name, selected: value });
      return next;
    });
    setExportResult(null);
  }
  function close() {
    setCategory(null);
    returnFocus.current?.focus();
  }
  /** 큰 파일은 fetch().blob()으로 받지 않고 브라우저 다운로드 관리자에 직접 맡깁니다. */
  async function exportSelection() {
    if (!selectedCount || exporting || expires) return;
    setError(""); setExportResult(null); setExporting(true);
    const abort = new AbortController(); controller.current = abort;
    try {
      let result = await client.export(job.id, {
        categories: categories.filter(([key]) => defaults[key]).map(([key]) => key),
        include_ids: [...overrides].filter(([, value]) => value.selected).map(([id]) => id),
        excluded_ids: [...overrides].filter(([, value]) => !value.selected).map(([id]) => id),
      }, abort.signal);
      while (result.status === "processing") {
        setExportResult(result);
        await new Promise(resolve => setTimeout(resolve, 800));
        if (abort.signal.aborted) return;
        result = await client.exportStatus(job.id, result.id, abort.signal);
      }
      if (result.status !== "completed") throw new Error(result.error || "ZIP을 만들지 못했습니다.");
      if (!abort.signal.aborted) {
        setExportResult(result);
        // 새 페이지로 이동하지 않는 숨은 프레임을 대상으로 직접 파일을 전송합니다.
        downloadRef.current.href = client.downloadUrl(result.download_url);
        downloadRef.current.click();
      }
    } catch (reason) {
      if (!abort.signal.aborted) setError(reason.message);
    } finally {
      if (!abort.signal.aborted) setExporting(false);
    }
  }
  const label = categories.find(([key]) => key === category)?.[1];
  return <section className="result-screen" aria-labelledby="result-title">
    <div className="success-mark">✓</div><p className="eyebrow">03 / 정리 완료</p>
    <h1 id="result-title">{total ? "정리가 끝났어요!" : "분류할 수 있는 사진이 없었어요."}</h1>
    <p className="screen-description">{total.toLocaleString()}장의 사진을 분류했어요. 폴더를 눌러 사진을 확인하고 받을 사진을 골라주세요.</p>
    <div className="result-counts">{categories.map(([key, name]) =>
      <button type="button" className={"folder-result review-card " + key} key={key} disabled={expires || exporting}
        aria-label={name + " 사진 상세보기"} onClick={event => { returnFocus.current = event.currentTarget; setPage(0); setCategory(key); }}>
        <span className="folder-shape"><svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><path d="M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z" /></svg></span><span>{name}<small>{key}</small></span><strong>{(job.counts?.[key] || 0).toLocaleString()}장</strong>
        <span className="review-hint">사진 확인하기 →</span></button>)}</div>
    <p className="failure-summary">실패 {job.failed_count || 0}개 · CSV 보고서에서 이유를 확인할 수 있어요.</p>
    <div className="download-choice"><h2>정리된 사진을 ZIP으로 받을까요?</h2>
      <p>선택한 사진 <b>{selectedCount.toLocaleString()}장</b> / 전체 {total.toLocaleString()}장</p>
      <div className="selection-actions"><button disabled={exporting || expires} onClick={() => { setDefaults(all()); setOverrides(new Map()); setExportResult(null); }}>전체 선택</button>
        <button disabled={exporting || expires} onClick={() => { setDefaults(Object.fromEntries(categories.map(([key]) => [key, false]))); setOverrides(new Map()); setExportResult(null); }}>전체 해제</button></div>
      <div className="choice-buttons"><button className="primary" disabled={!selectedCount || exporting || expires} onClick={exportSelection}>
        {exporting ? "선택 ZIP 만드는 중…" : "선택한 사진 ZIP 다운로드 ↓"}</button>
        <a className="secondary" href={!expires ? downloadUrl : undefined} target="photo-download" download aria-label="결과 ZIP 다운로드" aria-disabled={expires}>전체 결과 ZIP 다운로드</a>
        <button className="secondary" disabled={exporting} onClick={onBack}>지금은 안 받을게요</button></div>
      {exporting && <div className="gauge-panel" aria-live="polite"><p>선택한 사진을 ZIP 하나로 묶고 있어요.</p>
        <progress aria-label="선택 ZIP 생성 진행률" max="100" value={exportResult?.progress?.percent ?? 0} />
        <span>{Math.floor(exportResult?.progress?.percent ?? 0)}%</span></div>}
      {exportResult?.status === "completed" && <p role="status">ZIP이 준비됐어요. 다운로드가 시작되지 않았다면 <a href={client.downloadUrl(exportResult.download_url)} target="photo-download" download>여기를 눌러 저장</a>하세요.</p>}
      {error && <p className="error" role="alert">{error}</p>}
      <p className="hint">{expires ? "보관 기간이 끝났어요. 다시 업로드해 주세요." : job.expires_at ? new Date(job.expires_at * 1000).toLocaleString("ko-KR") + "까지 받을 수 있어요." : "보관 기간 안에 다운로드해 주세요."}</p>
    </div>
    <a ref={downloadRef} target="photo-download" download hidden>선택 결과 저장</a>
    <iframe name="photo-download" title="파일 다운로드" hidden />
    <dialog ref={dialogRef} className="review-dialog" aria-label={label + " 사진 상세보기"} onCancel={event => { event.preventDefault(); close(); }}>
      <header><div><h2>{label}</h2><p>선택은 다른 페이지와 분류에서도 유지돼요.</p></div><button className="secondary" onClick={close}>닫기</button></header>
      <div className="selection-actions"><button disabled={exporting} onClick={() => selectCategory(true)}>이 분류 전체 선택</button><button disabled={exporting} onClick={() => selectCategory(false)}>이 분류 전체 해제</button>
        <span>전체 선택 {selectedCount.toLocaleString()}장</span></div>
      {error && <p role="alert" className="error">{error}</p>}
      {!listing ? <p role="status">사진을 불러오고 있어요.</p> : !listing.total ? <p>이 분류에는 사진이 없어요.</p> : <>
        <div className="review-grid">{listing.items.map(item => <label className={"review-photo" + (selected(item) ? " selected" : "")} key={item.id}>
          <img src={client.downloadUrl(item.thumbnail_url)} alt={item.name} loading="lazy" decoding="async" />
          <span><input type="checkbox" checked={selected(item)} disabled={exporting} onChange={() => toggle(item)} aria-label={item.name + " 다운로드 선택"} />
            <span title={item.name}>{item.name}</span></span><small>{formatBytes(item.size)}</small></label>)}</div>
        <div className="pagination"><button disabled={!page} onClick={() => setPage(page - 1)}>이전</button><span>{page + 1} / {listing.pages}</span>
          <button disabled={page + 1 >= listing.pages} onClick={() => setPage(page + 1)}>다음</button></div></>}
      <footer><button className="primary" onClick={close}>선택 적용하고 닫기</button></footer>
    </dialog>
  </section>;
}
