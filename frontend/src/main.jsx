import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import "./styles.css";

// 상태·선택·업로드 처리를 React 컴포넌트에서 관리합니다.
createRoot(document.getElementById("root")).render(
  <React.StrictMode><App /></React.StrictMode>,
);
