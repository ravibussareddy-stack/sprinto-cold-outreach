// Every API route requires the DASHBOARD_TOKEN env var to match the
// x-dashboard-token header. If the variable is unset, nothing is allowed.
function authorized(req) {
  const want = process.env.DASHBOARD_TOKEN || "";
  const got = req.headers["x-dashboard-token"] || "";
  return want.length >= 16 && got === want;
}

module.exports = { authorized };
