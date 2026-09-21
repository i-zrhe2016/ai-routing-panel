export function createEmptyPortForm() {
  return {
    listen_port: "",
    expires_at: "",
    traffic_limit: "",
    note: "",
  };
}

export function createEmptyPlanForm() {
  return {
    slug: "",
    name: "",
    description: "",
    price_fen: "",
    duration_days: "",
    traffic_limit: "",
    enabled: true,
    sort_order: "0",
  };
}
