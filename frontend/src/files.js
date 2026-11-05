/** 바이트 크기를 화면에 표시할 단위로 바꿉니다. 입력 파일은 변경하지 않습니다. */
export function formatBytes(value) {
  if (value < 1024) return value + " B";
  if (value < 1024 ** 2) return (value / 1024).toFixed(1) + " KB";
  if (value < 1024 ** 3) return (value / 1024 ** 2).toFixed(1) + " MB";
  return (value / 1024 ** 3).toFixed(1) + " GB";
}

/** 파일·기존 목록·서버 제한을 받아 추가 가능한 목록과 제외 이유를 반환합니다. */
export function selectFiles(incoming, existing, limits) {
  const accepted = [];
  const rejected = [];
  let total = existing.reduce((sum, item) => sum + item.file.size, 0);
  const extensions = [...limits.extensions, ...(limits.archive_extensions || [])];
  for (const file of incoming) {
    const extension = "." + file.name.split(".").pop().toLowerCase();
    const isZip = extension === ".zip";
    let reason = "";
    if (!extensions.includes(extension)) reason = "지원하지 않는 형식";
    else if (file.size > (isZip ? limits.max_archive_bytes : limits.max_file_bytes)) reason = "개별 용량 초과";
    else if (existing.length + accepted.length >= limits.max_files) reason = "파일 수 초과";
    else if (total + file.size > limits.max_request_bytes) reason = "전체 용량 초과";
    if (reason) rejected.push(file.name + " (" + reason + ")");
    else {
      accepted.push({ id: crypto.randomUUID(), file, isZip });
      total += file.size;
    }
  }
  return { accepted, rejected };
}

/** ZIP 내부 장수는 추측하지 않고, 선택한 사진 수·ZIP 수·총 바이트를 반환합니다. */
export function selectionSummary(items) {
  const archives = items.filter((item) => item.isZip).length;
  return {
    photos: items.length - archives,
    archives,
    bytes: items.reduce((sum, item) => sum + item.file.size, 0),
  };
}
