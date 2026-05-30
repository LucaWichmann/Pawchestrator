import { REINJECT_DEBOUNCE_MS } from "./constants";
import { stopIssueStatusPolling, stopPrStatusPolling } from "./poll";
import { state } from "./state";

type InjectControls = () => void;

export function scheduleInjection(injectControls: InjectControls) {
  const pathnameChanged = state.activePathname !== window.location.pathname;
  if (pathnameChanged) {
    state.activePathname = window.location.pathname;
    state.panelExpandedByUser = null;
    state.lastPipelineExpansionKey = null;
    stopIssueStatusPolling();
    stopPrStatusPolling();
  }

  if (state.reinjectTimer) {
    window.clearTimeout(state.reinjectTimer);
  }

  state.reinjectTimer = window.setTimeout(
    () => {
      state.reinjectTimer = null;
      injectControls();
    },
    pathnameChanged ? 0 : REINJECT_DEBOUNCE_MS,
  );
}

export function installNavigationHooks(injectControls: InjectControls) {
  (["pushState", "replaceState"] as const).forEach((method) => {
    const original = history[method];
    history[method] = function (...args) {
      const result = original.apply(this, args);
      scheduleInjection(injectControls);
      return result;
    };
  });

  ["turbo:load", "turbo:render", "popstate"].forEach((eventName) => {
    window.addEventListener(eventName, () => scheduleInjection(injectControls));
  });
}
