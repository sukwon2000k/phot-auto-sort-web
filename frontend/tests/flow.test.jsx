import React from "react";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import App from "../src/App.jsx";
import { ProgressView } from "../src/AppShell.jsx";

const limits = {
  extensions: [".png"], archive_extensions: [".zip"], max_files: 5000,
  max_file_bytes: 20 * 1024 ** 2, max_archive_bytes: 2 * 1024 ** 3,
  max_request_bytes: 2 * 1024 ** 3, max_expanded_bytes: 4 * 1024 ** 3,
  max_pixels: 40000000, retention_seconds: 3600, upload_timeout_seconds: 1800,
};

/** 완료·진행 상태를 명시적으로 주입하는 화면 검증용 API입니다. */
function api(jobResult) {
  return {
    mode: "local",
    status: vi.fn().mockResolvedValue({ model_ready: true, busy: false, limits }),
    submit: vi.fn().mockResolvedValue({ id: "one", status: "processing" }),
    job: vi.fn().mockResolvedValue(jobResult),
    downloadUrl: (url) => url,
  };
}

describe("기능 우선 화면과 별도 페이지", () => {
  it("홈에서 설명을 펼치지 않고 사용방법 페이지로 이동한다", async () => {
    const client = api({});
    render(<App client={client} />);
    await screen.findByText(/사진이나 ZIP을 선택하면/);
    expect(screen.queryByText("세 폴더의 기준")).toBeNull();
    await userEvent.click(screen.getByRole("link", { name: "사용방법", exact: true }));
    expect(window.location.pathname).toBe("/guide");
    expect(screen.getByRole("heading", { name: "사진 정리함, 이렇게 써요." })).toBeTruthy();
    expect(screen.getByText("세 폴더의 기준")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: /사진 정리하러 가기/ }));
    expect(window.location.pathname).toBe("/");
    expect(screen.getByRole("button", { name: "사진·ZIP 선택" })).toBeTruthy();
  });

  it("처리 시작 시 진행 화면과 실제 서버 진행률을 표시한다", async () => {
    render(<App client={api({ id: "one", status: "processing", progress: {
      phase: "classifying", processed: 1250, total: 5000, percent: 25,
    } })} />);
    await screen.findByText(/사진이나 ZIP을 선택하면/);
    fireEvent.change(screen.getByLabelText("사진 및 ZIP 파일"), { target: { files: [new File(["x"], "x.zip")] } });
    await userEvent.click(screen.getByRole("button", { name: /분류 시작/ }));
    await waitFor(() => expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe("25"));
    expect(screen.getByText("25%")).toBeTruthy();
    expect(document.querySelector(".gauge-detail").textContent).toBe("전체 5,000개 중 1,250개 처리");
    expect(window.location.pathname).toBe("/processing");
  });

  it("완료 시 세 종류의 개수와 다운로드 여부 선택 화면을 표시한다", async () => {
    sessionStorage.setItem("photo-sorter-last-job-local", "one");
    const client = api({
      id: "one", status: "completed", counts: { landscape: 1200, person: 350, food: 40 }, failed_count: 2,
      expires_at: Date.now() / 1000 + 3600, download_url: "/api/jobs/one/download",
    });
    render(<App client={client} />);
    await screen.findByRole("heading", { name: "정리가 끝났어요!" });
    expect(window.location.pathname).toBe("/result");
    expect(document.querySelector(".folder-result.landscape").textContent).toContain("1,200");
    expect(document.querySelector(".folder-result.person").textContent).toContain("350");
    expect(document.querySelector(".folder-result.food").textContent).toContain("40");
    expect(screen.getByText("정리된 사진을 ZIP으로 받을까요?")).toBeTruthy();
    expect(screen.getByRole("link", { name: "결과 ZIP 다운로드" }).getAttribute("href")).toBe("/api/jobs/one/download");
    // 자동 다운로드나 새 분류 작업은 시작하지 않습니다.
    expect(client.submit).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "지금은 안 받을게요" }));
    expect(window.location.pathname).toBe("/");
    expect(screen.queryByRole("heading", { name: "정리가 끝났어요!" })).toBeNull();
    await userEvent.click(screen.getByRole("link", { name: /결과 다시 보기/ }));
    expect(window.location.pathname).toBe("/result");
  });
});

describe("가짜 퍼센트를 만들지 않는 게이지", () => {
  it("실제 접수한 작업이 없으면 진행 중인 것처럼 표시하지 않는다", () => {
    render(<ProgressView phase="idle" onBack={() => {}} />);
    expect(screen.getByText("진행 중인 작업이 없어요.")).toBeTruthy();
    expect(screen.queryByRole("progressbar")).toBeNull();
  });
  it("전체 수가 불명확하면 수치 없는 준비 표시를 사용한다", () => {
    render(<ProgressView job={{ progress: { phase: "extracting", processed: 0, total: null, percent: null } }} />);
    expect(screen.getByRole("progressbar").hasAttribute("aria-valuenow")).toBe(false);
    expect(screen.queryByText("0%")).toBeNull();
    expect(screen.getByText("ZIP 속 사진을 꺼내고 있어요.")).toBeTruthy();
  });

  it("100% 후에도 ZIP 생성 중에는 완료 선택 화면을 보여주지 않는다", () => {
    render(<ProgressView job={{ progress: { phase: "zipping", processed: 10, total: 10, percent: 100 } }} />);
    expect(screen.getByText("100%")).toBeTruthy();
    expect(screen.getByText("분류 완료! ZIP을 준비해요.")).toBeTruthy();
    expect(screen.queryByText("정리된 사진을 ZIP으로 받을까요?")).toBeNull();
  });
});
