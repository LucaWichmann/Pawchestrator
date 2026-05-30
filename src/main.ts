import "./styles";
import { injectControls } from "./controls";
import { installNavigationHooks, scheduleInjection } from "./navigation";

(function () {
  "use strict";

  injectControls();
  installNavigationHooks(injectControls);

  const observer = new MutationObserver(() => {
    scheduleInjection(injectControls);
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });
})();
