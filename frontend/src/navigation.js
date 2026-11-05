import { useCallback, useEffect, useState } from "react";

/** 네 개의 작은 화면을 URL과 연결하고 브라우저 뒤로 가기를 처리합니다. */
export function useRoute() {
  const [route, setRoute] = useState(window.location.pathname);
  useEffect(() => {
    const onPop = () => setRoute(window.location.pathname);
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  const navigate = useCallback((path, replace = false) => {
    if (window.location.pathname !== path) {
      window.history[replace ? "replaceState" : "pushState"]({}, "", path);
    }
    setRoute(path);
    window.scrollTo?.({ top: 0, behavior: "instant" });
  }, []);
  return [route, navigate];
}
