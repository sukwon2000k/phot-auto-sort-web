import { afterEach, describe, expect, it, vi } from "vitest";
import { createApi } from "../src/api.js";

/** 테스트에서 XHR 응답을 제어하고 실제 네트워크 호출은 하지 않습니다. */
class XhrStub {
  constructor() { this.upload = {}; this.headers = {}; }
  open(method, url) { this.method = method; this.url = url; }
  setRequestHeader(name, value) { this.headers[name] = value; }
  send(body) {
    this.body = body;
    requests.push(this);
    queueMicrotask(() => {
      this.upload.onload?.();
      this.status = 200;
      this.responseText = JSON.stringify({ id: "local1", status: "processing" });
      this.onload();
    });
  }
  abort() { this.onabort?.(); }
}
let requests = [];
afterEach(() => { requests = []; vi.unstubAllGlobals(); });

describe("로컬·AWS API 어댑터", () => {
  it("local은 기존 multipart API로 보낸다", async () => {
    vi.stubGlobal("XMLHttpRequest", XhrStub);
    const phase = vi.fn();
    const result = await createApi().submit([new File(["x"], "x.zip")], { onPhase: phase });
    expect(result.id).toBe("local1");
    expect(requests[0].url).toBe("/api/jobs");
    expect(requests[0].body instanceof FormData).toBe(true);
    expect(phase).toHaveBeenCalled();
  });

  it("cloud는 세션 생성→S3 PUT→작업 접수 순서로 호출한다", async () => {
    vi.stubGlobal("XMLHttpRequest", XhrStub);
    const fetchStub = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ({
        id: "session1", uploads: [{ method: "PUT", url: "https://bucket.example/one", headers: { "Content-Type": "image/png" } }],
      }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ id: "cloud1", status: "queued" }) });
    vi.stubGlobal("fetch", fetchStub);
    const result = await createApi({ baseUrl: "https://api.example", mode: "cloud" })
      .submit([new File(["x"], "x.png", { type: "image/png" })]);
    expect(fetchStub.mock.calls[0][0]).toBe("https://api.example/api/upload-sessions");
    expect(requests[0].method).toBe("PUT");
    expect(requests[0].body instanceof File).toBe(true);
    expect(JSON.parse(fetchStub.mock.calls[1][1].body)).toEqual({ session_id: "session1" });
    expect(result.status).toBe("queued");
  });

  it("cloud 업로드 실패 후 GPU 작업을 접수하지 않는다", async () => {
    class FailingXhr extends XhrStub {
      send() { queueMicrotask(() => { this.status = 403; this.onload(); }); }
    }
    vi.stubGlobal("XMLHttpRequest", FailingXhr);
    const fetchStub = vi.fn().mockResolvedValueOnce({ ok: true, json: async () => ({
      id: "session1", uploads: [{ method: "PUT", url: "https://bucket.example/one" }],
    }) });
    vi.stubGlobal("fetch", fetchStub);
    await expect(createApi({ mode: "cloud" }).submit([new File(["x"], "x.png")])).rejects.toThrow("업로드");
    expect(fetchStub).toHaveBeenCalledTimes(1);
  });

  it("비정상 다운로드 프로토콜을 거부한다", () => {
    const api = createApi();
    expect(() => api.downloadUrl("javascript:alert(1)")).toThrow();
    expect(() => api.downloadUrl("//other.example/file.zip")).toThrow();
    expect(api.downloadUrl("https://storage.example/result.zip?sig=x")).toContain("https://");
  });
});
