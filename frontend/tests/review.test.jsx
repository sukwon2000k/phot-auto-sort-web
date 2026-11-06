import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ResultReview from "../src/ResultReview.jsx";
import { ProgressView } from "../src/AppShell.jsx";

describe("분류 상세보기와 선택 ZIP", () => {
  it("분류를 바꿔도 사진 선택을 유지하고 선택 조건만 서버에 보낸다", async () => {
    const client = {
      items: vi.fn(async (_, category) => ({ total: 1, pages: 1, items: [{ id: category === "food" ? 0 : 1,
        name: category + ".png", class_name: category, size: 50, thumbnail_url: "/thumb" }] })),
      export: vi.fn(async () => ({ id: "zip", status: "completed", download_url: "/selected.zip" })),
      downloadUrl: value => value,
    };
    // 브라우저 다운로드 이동을 막고 링크 생성과 클릭 동작만 검사합니다.
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    render(<ResultReview job={{ id: "job", status: "completed", counts: { food: 1, person: 1, landscape: 0 } }} client={client} />);
    await userEvent.click(screen.getByRole("button", { name: "음식 사진 상세보기" }));
    const check = await screen.findByLabelText("food.png 다운로드 선택");
    expect(check.checked).toBe(true);
    await userEvent.click(check);
    await userEvent.click(screen.getByRole("button", { name: "닫기", exact: true }));
    await userEvent.click(screen.getByRole("button", { name: "인물 사진 상세보기" }));
    expect((await screen.findByLabelText("person.png 다운로드 선택")).checked).toBe(true);
    await userEvent.click(screen.getByRole("button", { name: "닫기", exact: true }));
    await userEvent.click(screen.getByRole("button", { name: "선택한 사진 ZIP 다운로드 ↓" }));
    await waitFor(() => expect(client.export).toHaveBeenCalled());
    expect(client.export.mock.calls[0][1]).toEqual({ categories: ["landscape", "person", "food"], include_ids: [], excluded_ids: [0] });
    expect(screen.getByRole("link", { name: "여기를 눌러 저장" }).getAttribute("target")).toBe("photo-download");
  });

  it("실제 업로드 바이트를 막대에 표시한다", () => {
    render(<ProgressView phase="uploading" uploadProgress={{ phase: "uploading", processed: 50*1024**2, total: 100*1024**2, percent: 50 }} />);
    expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe("50");
    expect(screen.getByText("50.0 MB / 100.0 MB")).toBeTruthy();
  });
});
