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
  it("local은 조각 업로드 후 분류 작업을 접수한다", async () => {
    vi.stubGlobal("XMLHttpRequest", XhrStub);
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ({ id: "upload1", chunk_bytes: 2 }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ id: "local1", status: "processing" }) }));
    const progress = vi.fn();
    const result = await createApi().submit([new File(["abcde"], "x.zip")], { onProgress: progress });
    expect(result.id).toBe("local1");
    expect(requests.map(x => x.url)).toEqual(["/api/uploads/upload1/0/0", "/api/uploads/upload1/0/1", "/api/uploads/upload1/0/2"]);
    expect(requests.map(x => x.body.size)).toEqual([2, 2, 1]);
    expect(progress.mock.calls.at(-1)[0].percent).toBe(100);
  });

  it("가상 3GB 파일도 원본 전체를 읽지 않고 100MB 미만 요청으로 보낸다", async () => {
    vi.stubGlobal("XMLHttpRequest", XhrStub);
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ({ id: "big", chunk_bytes: 50*1024**2 }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ id: "big", status: "processing" }) }));
    // 3GB 메모리를 할당하지 않고 File.slice의 구간과 전송 크기를 검증합니다.
    const file = { name: "3gb.zip", size: 3*1024**3, lastModified: 1, slice: (start,end) => ({ size:end-start }) };
    await createApi().submit([file]);
    expect(requests.length).toBe(62);
    expect(requests.every(x => x.body.size < 100000000)).toBe(true);
    expect(requests.reduce((sum,x) => sum+x.body.size,0)).toBe(file.size);
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
