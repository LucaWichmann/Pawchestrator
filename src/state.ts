export const state = {
  activePoll: null,
  activePrPoll: null,
  activeRunId: null,
  activeRunStream: null as EventSource | null,
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
  runLogLines: [] as string[],
  sseConnected: false,
  planAttempt: 1,
  planAttemptRunId: null,
  rejectedPlanRunIds: new Set(),
};
