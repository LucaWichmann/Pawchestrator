import { CONFIRM_OVERLAY_ID } from "../constants";
import { createButton } from "./common";

function createDialogButton(labelText, variant, onClick) {
  const button = createButton("", "", labelText, onClick);
  button.removeAttribute("id");
  delete button.dataset.testid;
  if (variant === "danger") {
    button.classList.add("pawchestrator-confirm-danger");
  }
  return button;
}

export function showConfirmDialog(message, options = {}) {
  document.getElementById(CONFIRM_OVERLAY_ID)?.remove();

  return new Promise((resolve) => {
    const overlay = document.createElement("div");
    overlay.id = CONFIRM_OVERLAY_ID;
    overlay.setAttribute("role", "presentation");

    const dialog = document.createElement("div");
    dialog.className = "pawchestrator-confirm-dialog";
    dialog.setAttribute("role", "dialog");
    dialog.setAttribute("aria-modal", "true");
    dialog.setAttribute("aria-labelledby", "pawchestrator-confirm-title");
    dialog.setAttribute("aria-describedby", "pawchestrator-confirm-message");

    const header = document.createElement("div");
    header.id = "pawchestrator-confirm-title";
    header.className = "pawchestrator-confirm-header";
    header.textContent = options.title || "Confirm action";

    const body = document.createElement("div");
    body.id = "pawchestrator-confirm-message";
    body.className = "pawchestrator-confirm-body";
    body.textContent = message;

    const actions = document.createElement("div");
    actions.className = "pawchestrator-confirm-actions";

    let settled = false;
    const close = (confirmed) => {
      if (settled) {
        return;
      }
      settled = true;
      document.removeEventListener("keydown", onKeydown);
      overlay.remove();
      resolve(confirmed);
    };
    const onKeydown = (event) => {
      if (event.key === "Escape") {
        close(false);
      }
    };

    const noButton = createDialogButton(options.cancelLabel || "No", "default", () => close(false));
    const yesButton = createDialogButton(options.confirmLabel || "Yes", "danger", () =>
      close(true),
    );
    actions.append(noButton, yesButton);

    dialog.append(header, body, actions);
    overlay.append(dialog);
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) {
        close(false);
      }
    });
    document.addEventListener("keydown", onKeydown);
    document.documentElement.append(overlay);
    noButton.focus();
  });
}
