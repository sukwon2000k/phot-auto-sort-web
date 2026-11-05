import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import App from "../src/App.jsx";
import { selectFiles } from "../src/files.js";

const limits = {
  extensions: [".jpg", ".jpeg", ".jfif", ".png", ".webp"], archive_extensions: [".zip"],
  max_files: 5000, max_file_bytes: 20 * 1024 ** 2, max_archive_bytes: 2 * 1024 ** 3,
  max_request_bytes: 2 * 1024 ** 3, max_expanded_bytes: 4 * 1024 ** 3,
  max_pixels: 40000000, retention_seconds: 3600, upload_timeout_seconds: 1800,
};

/** 테스트가 제어하는 클라이언트를 주입합니다. 운영 앱에는 모의 분류를 넣지 않습니다. */
function client(overrides = {}) {
  return {
    mode: "local",
    status: vi.fn().mockResolvedValue({ model_ready: true, busy: false, limits }),
    submit: vi.fn().mockResolvedValue({ id: "job1", status: "processing" }),
    job: vi.fn().mockResolvedValue({
      id: "job1", status: "completed", counts: { food: 2, landscape: 0, person: 1 },
      failed_count: 1, expires_at: Date.now() / 1000 + 3600,
      download_url: "/api/jobs/job1/download",
    }),
    downloadUrl: (url) => url,
    ...overrides,
  };
}

describe("React 사진 정리 흐름", () => {
  it("모델이 없으면 선택은 가능하고 분류는 차단한다", async () => {
    render(<App client={client({ status: vi.fn().mockResolvedValue({
      model_ready: false, busy: false, limits, message: "분류 기능 준비 중입니다.",
    }) })} />);
    await screen.findByText("분류 기능 준비 중입니다.");
    fireEvent.change(screen.getByLabelText("사진 및 ZIP 파일"), { target: {
      files: [new File(["image"], "photo.png", { type: "image/png" })],
    } });
    expect(screen.getByRole("button", { name: /분류 시작/ }).disabled).toBe(true);
    expect(screen.getByText("photo.png")).toBeTruthy();
  });

  it("ZIP과 사진을 선택·제거·초기화한다", async () => {
    render(<App client={client()} />);
    await screen.findByText(/사진이나 ZIP을 선택하면/);
    fireEvent.change(screen.getByLabelText("사진 및 ZIP 파일"), { target: {
      files: [new File(["zip"], "photos.zip"), new File(["image"], "photo.png")],
    } });
    expect(document.querySelector(".selection-tools").textContent).toContain("사진 1장 · ZIP 1개");
    await userEvent.click(screen.getByRole("button", { name: "photo.png 제거" }));
    expect(screen.queryByText("photo.png")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "전체 초기화" }));
    expect(screen.queryByText("photos.zip")).toBeNull();
  });

  it("업로드·서버 조회 후 개수와 결과 링크를 보여준다", async () => {
    const api = client();
    render(<App client={api} />);
    await screen.findByText(/사진이나 ZIP을 선택하면/);
    fireEvent.change(screen.getByLabelText("사진 및 ZIP 파일"), { target: {
      files: [new File(["zip"], "photos.zip")],
    } });
    await userEvent.click(screen.getByRole("button", { name: /분류 시작/ }));
    const link = await screen.findByRole("link", { name: /결과 ZIP 다운로드/ });
    expect(link.getAttribute("href")).toBe("/api/jobs/job1/download");
    expect(api.submit.mock.calls[0][0][0].name).toBe("photos.zip");
    expect(api.job).toHaveBeenCalledWith("job1", expect.any(AbortSignal));
    expect(screen.getByText("정리가 끝났어요!")).toBeTruthy();
    expect(window.location.pathname).toBe("/result");
    expect(document.querySelector(".failure-summary").textContent).toContain("실패 1개");
  });

  it("몇천 장을 선택해도 미리보기는 페이지당 60개만 만든다", async () => {
    render(<App client={client()} />);
    await screen.findByText(/사진이나 ZIP을 선택하면/);
    fireEvent.change(screen.getByLabelText("사진 및 ZIP 파일"), { target: {
      files: Array.from({ length: 2000 }, (_, i) => new File(["x"], i + ".png")),
    } });
    expect(screen.getAllByRole("img").length).toBe(60);
    expect(screen.getByText("1 / 34")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "다음" }));
    expect(screen.getAllByRole("img").length).toBe(60);
    expect(screen.getByText("2 / 34")).toBeTruthy();
    expect(screen.getByText("60.png")).toBeTruthy();
    expect(screen.queryByText("0.png")).toBeNull();
    expect(URL.revokeObjectURL).toHaveBeenCalled();
  });

  it("업로드 실패를 표시하고 다시 조작할 수 있게 한다", async () => {
    render(<App client={client({ submit: vi.fn().mockRejectedValue(new Error("업로드 제한 초과")) })} />);
    await screen.findByText(/사진이나 ZIP을 선택하면/);
    fireEvent.change(screen.getByLabelText("사진 및 ZIP 파일"), { target: { files: [new File(["x"], "x.png")] } });
    await userEvent.click(screen.getByRole("button", { name: /분류 시작/ }));
    expect((await screen.findByRole("alert")).textContent).toBe("업로드 제한 초과");
    await userEvent.click(screen.getByRole("button", { name: /사진 선택으로 돌아가기/ }));
    expect(screen.getByRole("button", { name: /분류 시작/ }).disabled).toBe(false);
  });

  it("AWS 작업 대기 상태를 실제 상태 이름으로 표시한다", async () => {
    render(<App client={client({ mode: "cloud", job: vi.fn().mockResolvedValue({
      id: "job1", status: "provisioning",
    }) })} />);
    await screen.findByText(/사진이나 ZIP을 선택하면/);
    fireEvent.change(screen.getByLabelText("사진 및 ZIP 파일"), { target: { files: [new File(["x"], "x.zip")] } });
    await userEvent.click(screen.getByRole("button", { name: /분류 시작/ }));
    expect(await screen.findByText(/분류 서버를 준비하고 있어요/)).toBeTruthy();
    expect(window.location.pathname).toBe("/processing");
    expect(screen.queryByRole("button", { name: /분류 시작/ })).toBeNull();
  });

  it("새로 고쳐도 같은 탭에서 작업 ID로 상태를 복원한다", async () => {
    sessionStorage.setItem("photo-sorter-last-job-local", "restored");
    const api = client();
    render(<App client={api} />);
    await screen.findByRole("link", { name: /결과 ZIP 다운로드/ });
    expect(api.job).toHaveBeenCalledWith("restored", expect.any(AbortSignal));
  });

  it("만료된 결과는 다운로드 링크를 비활성화한다", async () => {
    sessionStorage.setItem("photo-sorter-last-job-local", "job1");
    render(<App client={client({ job: vi.fn().mockResolvedValue({
      id: "job1", status: "completed", counts: { person: 1 }, expires_at: 1,
      download_url: "/api/jobs/job1/download",
    }) })} />);
    const link = await screen.findByText("결과 ZIP 다운로드 ↓");
    await waitFor(() => expect(link.hasAttribute("href")).toBe(false));
    expect(screen.getByText(/보관 기간이 끝났어요/)).toBeTruthy();
  });
});

describe("서버 제한에 맞춘 선택", () => {
  it("ZIP과 사진의 개별 용량을 다르게 검사한다", () => {
    const result = selectFiles([
      { name: "image.png", size: 30 }, { name: "photos.zip", size: 30 },
    ], [], { ...limits, max_file_bytes: 20, max_archive_bytes: 40 });
    expect(result.accepted.map((item) => item.file.name)).toEqual(["photos.zip"]);
    expect(result.rejected).toHaveLength(1);
  });

  it("파일 수·전체 용량·미지원 확장자를 검사한다", () => {
    const result = selectFiles([
      { name: "x.rar", size: 1 }, { name: "a.png", size: 5 }, { name: "b.png", size: 5 },
    ], [], { ...limits, max_files: 1, max_request_bytes: 9 });
    expect(result.accepted).toHaveLength(1);
    expect(result.rejected).toHaveLength(2);
  });
});
