export function parseIssueReference() {
  const [, owner, repo, type, number] = window.location.pathname.split("/");
  if (!owner || !repo || type !== "issues" || !number) {
    throw new Error("Not a GitHub issue page");
  }

  const issueNumber = Number.parseInt(number, 10);
  if (!Number.isInteger(issueNumber) || issueNumber <= 0) {
    throw new Error("Invalid GitHub issue number");
  }

  return { owner, repo, number: issueNumber };
}

export function isIssuePage() {
  const [, owner, repo, type, number, extra] = window.location.pathname.split("/");
  const issueNumber = Number.parseInt(number, 10);
  return (
    Boolean(owner) &&
    Boolean(repo) &&
    type === "issues" &&
    String(issueNumber) === number &&
    issueNumber > 0 &&
    !extra
  );
}

export function parsePrReference() {
  const [, owner, repo, type, number, extra] = window.location.pathname.split("/");
  if (!owner || !repo || type !== "pull" || extra) {
    throw new Error("Not a GitHub pull request page");
  }

  const prNumber = Number.parseInt(number, 10);
  if (!Number.isInteger(prNumber) || String(prNumber) !== number || prNumber <= 0) {
    throw new Error("Invalid GitHub pull request number");
  }

  return { owner, repo, pr_number: prNumber };
}

export function isPrPage() {
  try {
    parsePrReference();
    return true;
  } catch {
    return false;
  }
}
