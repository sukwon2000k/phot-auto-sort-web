import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";

// jsdom에는 실제 이미지 디코딩이 없으므로 URL 수명 관리만 대역으로 검증합니다.
URL.createObjectURL = vi.fn(() => "blob:test-preview");
URL.revokeObjectURL = vi.fn();
beforeEach(() => {
  window.history.replaceState({}, "", "/");
  window.scrollTo = vi.fn();
});
afterEach(() => {
  cleanup();
  sessionStorage.clear();
  vi.restoreAllMocks();
});
