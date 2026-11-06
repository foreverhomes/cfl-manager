import "./styles.css";
import "./legacy/app.js";

if (import.meta.env.DEV && location.search.includes("mock-draft")) {
  import("./views/draft/mock").then(({ startDraftMock }) => startDraftMock());
}
