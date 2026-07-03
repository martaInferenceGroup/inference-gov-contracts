/**
 * Cloudflare Worker — Draft Application Trigger
 * ================================================
 * Receives GET requests from weekly email "Draft" buttons and triggers
 * the draft-application GitHub Action for the specified contract.
 *
 * URL format:
 *   https://<worker>.workers.dev/draft?contract=4&week=2026-07-03
 *
 * Environment variables (set as Worker secrets):
 *   GITHUB_PAT        — GitHub Personal Access Token with 'repo' scope
 *   TRIGGER_SECRET     — Random string to prevent unauthorized triggers
 *
 * Deploy:
 *   1. npx wrangler login
 *   2. npx wrangler deploy scripts/draft-trigger-worker.js --name gov-contracts-draft
 *   3. npx wrangler secret put GITHUB_PAT
 *   4. npx wrangler secret put TRIGGER_SECRET
 */

const REPO_OWNER = "martaInferenceGroup";
const REPO_NAME = "inference-gov-contracts";
const WORKFLOW_FILE = "draft-application.yml";
const BRAND_BLUE = "#30475E";
const BRAND_ORANGE = "#D08770";

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // Only handle /draft path
    if (url.pathname !== "/draft") {
      return htmlResponse("Not found", 404, "This endpoint only handles /draft requests.");
    }

    // Extract parameters
    const contract = url.searchParams.get("contract");
    const week = url.searchParams.get("week") || "";
    const token = url.searchParams.get("token") || "";

    // Validate
    if (!contract || isNaN(parseInt(contract))) {
      return htmlResponse("Invalid request", 400, "Missing or invalid contract number.");
    }

    if (env.TRIGGER_SECRET && token !== env.TRIGGER_SECRET) {
      return htmlResponse("Unauthorized", 403, "Invalid trigger token. This link may have expired.");
    }

    const contractNum = parseInt(contract);

    // Trigger GitHub Actions workflow
    try {
      const ghResponse = await fetch(
        `https://api.github.com/repos/${REPO_OWNER}/${REPO_NAME}/actions/workflows/${WORKFLOW_FILE}/dispatches`,
        {
          method: "POST",
          headers: {
            "Authorization": `Bearer ${env.GITHUB_PAT}`,
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "gov-contracts-draft-trigger",
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            ref: "master",
            inputs: {
              contract_number: String(contractNum),
              week_date: week,
              test_mode: "true",
              template_type: "",
            },
          }),
        }
      );

      if (ghResponse.status === 204) {
        return htmlResponse(
          "Draft triggered",
          200,
          `Draft application for contract <strong>#${contractNum}</strong> has been triggered.
           <br><br>You will receive the draft by email shortly (usually 2-3 minutes).
           <br><br><small>You can close this page.</small>`
        );
      } else {
        const errorText = await ghResponse.text();
        console.error(`GitHub API error: ${ghResponse.status} ${errorText}`);
        return htmlResponse(
          "Trigger failed",
          502,
          `Could not trigger the draft workflow. GitHub returned status ${ghResponse.status}.
           <br><br>Try triggering manually from
           <a href="https://github.com/${REPO_OWNER}/${REPO_NAME}/actions/workflows/${WORKFLOW_FILE}">GitHub Actions</a>.`
        );
      }
    } catch (err) {
      console.error(`Worker error: ${err.message}`);
      return htmlResponse(
        "Error",
        500,
        `An unexpected error occurred: ${err.message}`
      );
    }
  },
};

function htmlResponse(title, status, message) {
  const icon = status === 200 ? "&#10003;" : "&#10007;";
  const iconColor = status === 200 ? "#1a7a3a" : "#dc3545";

  const html = `<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>${title} — Inference Group</title>
  <link href="https://fonts.googleapis.com/css2?family=Roboto:wght@400;600;700&display=swap" rel="stylesheet">
</head>
<body style="margin:0; padding:0; background:#f0f2f6; font-family:Roboto,Arial,sans-serif;">
  <div style="max-width:500px; margin:80px auto; background:white; border-radius:12px; overflow:hidden; box-shadow:0 2px 12px rgba(0,0,0,0.1);">
    <div style="background:linear-gradient(135deg,${BRAND_BLUE},#3d5a73); padding:24px; text-align:center;">
      <h1 style="color:white; margin:0; font-size:20px;">Gov Contracts — Draft Trigger</h1>
    </div>
    <div style="padding:32px; text-align:center;">
      <div style="font-size:48px; color:${iconColor}; margin-bottom:16px;">${icon}</div>
      <h2 style="color:${BRAND_BLUE}; margin:0 0 16px 0; font-size:18px;">${title}</h2>
      <p style="color:#555; font-size:14px; line-height:1.6;">${message}</p>
    </div>
    <div style="padding:16px; background:#f8f9fa; text-align:center; font-size:11px; color:#999;">
      Inference Group &bull; Automated Gov Contracts
    </div>
  </div>
</body>
</html>`;

  return new Response(html, {
    status,
    headers: { "Content-Type": "text/html;charset=UTF-8" },
  });
}
