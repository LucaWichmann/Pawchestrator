import { API_BASE, OFFLINE_MESSAGE, TOKEN_KEY } from "./constants";

type RequestOptions = {
  method?: string;
  label?: string;
  headers?: Record<string, string>;
  body?: string;
  statusSetter?: (message: string) => void;
};

type RequestError = Error & { status?: number };

const noopStatusSetter = () => {};

export function rawRequestJson(path: string, options: RequestOptions = {}) {
  return new Promise<any>((resolve, reject) => {
    GM_xmlhttpRequest({
      method: options.method || "GET",
      url: `${API_BASE}${path}`,
      headers: options.headers || {},
      data: options.body,
      timeout: 5000,
      onload: (response) => {
        if (response.status < 200 || response.status >= 300) {
          const error = new Error(
            `${options.label || "Request"} failed (${response.status})`,
          ) as RequestError;
          error.status = response.status;
          reject(error);
          return;
        }

        if (!response.responseText) {
          resolve(null);
          return;
        }

        try {
          resolve(JSON.parse(response.responseText));
        } catch (error) {
          reject(
            new Error(
              `${options.label || "Request"} returned invalid JSON: ${(error as Error).message}`,
            ),
          );
        }
      },
      onerror: () => reject(new Error(OFFLINE_MESSAGE)),
      ontimeout: () => reject(new Error(OFFLINE_MESSAGE)),
    });
  });
}

export async function getOrAcquireToken(statusSetter = noopStatusSetter) {
  const storedToken = await GM_getValue(TOKEN_KEY);
  if (storedToken) {
    return storedToken;
  }

  statusSetter("Pairing - approve in terminal...");
  const response = await rawRequestJson("/pair", {
    method: "POST",
    label: "Pairing request",
  });
  await GM_setValue(TOKEN_KEY, response.token);
  return response.token;
}

export async function requestJson(path: string, options: RequestOptions = {}) {
  if (path === "/health" || path === "/pair") {
    return rawRequestJson(path, options);
  }

  const statusSetter = options.statusSetter || noopStatusSetter;
  const token = await getOrAcquireToken(statusSetter);
  const headers = {
    ...(options.headers || {}),
    "X-Pawchestrator-Token": token,
  };

  try {
    return await rawRequestJson(path, { ...options, headers });
  } catch (error) {
    if ((error as RequestError).status !== 403) {
      throw error;
    }

    await GM_deleteValue(TOKEN_KEY);
    const freshToken = await getOrAcquireToken(statusSetter);
    return rawRequestJson(path, {
      ...options,
      headers: {
        ...(options.headers || {}),
        "X-Pawchestrator-Token": freshToken,
      },
    });
  }
}

export async function fetchIssueStatus(issue: { owner: string; repo: string; number: number }) {
  return requestJson(`/issue/${issue.owner}/${issue.repo}/${issue.number}/status`, {
    label: "Issue status request",
  });
}

export async function fetchPlan(runId: string) {
  return requestJson(`/runs/${runId}/plan`, {
    label: "Plan request",
  });
}

export async function fetchPrRun(runId: string) {
  return requestJson(`/runs/${runId}/status`, {
    label: "PR review status request",
  });
}

export async function fetchPrStatus(pr: { owner: string; repo: string; pr_number: number }) {
  return requestJson(`/pr/${pr.owner}/${pr.repo}/${pr.pr_number}/status`, {
    label: "PR status request",
  });
}

export async function fetchPrReviewState(pr: { owner: string; repo: string; pr_number: number }) {
  return requestJson(`/prs/${pr.owner}/${pr.repo}/${pr.pr_number}/review-state`, {
    label: "PR review state request",
  });
}
