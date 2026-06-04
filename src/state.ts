export const state = {
  activePoll: null,
  activePrPoll: null,
  activePathname: window.location.pathname,
  config: null as {
    pipeline: {
      verify_repair_attempts: number;
      plan_approval_max_attempts: number;
      smart_routing?: {
        enabled: boolean;
        confirm_skip: boolean;
      };
    };
  } | null,
  panelExpandedByUser: null,
  lastPipelineExpansionKey: null,
  reinjectTimer: null,
  grillReplyObserverState: null,
  latestIssueStatus: null,
  latestPrRun: null,
  latestPrReviewState: null,
  planAttempt: 1,
  planAttemptRunId: null,
  rejectedPlanRunIds: new Set(),
};
