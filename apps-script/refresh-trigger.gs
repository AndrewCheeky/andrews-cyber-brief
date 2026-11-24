/**
 * Optional: start the GitHub feed refresh as soon as the briefing sheet changes.
 * Paste into the spreadsheet's Apps Script project (Extensions > Apps Script).
 *
 * Setup (once):
 * 1. Create a fine-grained GitHub token limited to this repository with
 *    "Actions: Read and write" permission only.
 * 2. Apps Script > Project Settings > Script properties: add GITHUB_TOKEN = <token>.
 *    Never paste the token into this file or the repository.
 * 3. Run installTriggers() once and approve the permissions prompt.
 *
 * Note: Google does not fire change triggers for edits made by scripts. If an
 * automated job writes the briefing row, call refreshBriefingFeed() at the end
 * of that job instead of relying on onSheetChange.
 */
const GITHUB_REPO = 'AndrewCheeky/andrews-cyber-brief';  // update if the repository name differs
const WORKFLOW_FILE = 'publish.yml';

function refreshBriefingFeed() {
  const token = PropertiesService.getScriptProperties().getProperty('GITHUB_TOKEN');
  if (!token) throw new Error('GITHUB_TOKEN script property is not set');
  const response = UrlFetchApp.fetch(
    'https://api.github.com/repos/' + GITHUB_REPO + '/actions/workflows/' + WORKFLOW_FILE + '/dispatches',
    {
      method: 'post',
      contentType: 'application/json',
      headers: { Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json' },
      payload: JSON.stringify({ ref: 'main' }),
      muteHttpExceptions: true,
    });
  if (response.getResponseCode() !== 204) {
    throw new Error('GitHub refresh failed: ' + response.getResponseCode() + ' ' + response.getContentText());
  }
}

// Debounce: an edit burst while a briefing is being written triggers one refresh per 2 minutes.
function onSheetChange() {
  const cache = CacheService.getScriptCache();
  if (cache.get('recentRefresh')) return;
  cache.put('recentRefresh', '1', 120);
  refreshBriefingFeed();
}

function installTriggers() {
  ScriptApp.getProjectTriggers()
    .filter(t => ['onSheetChange', 'refreshBriefingFeed'].includes(t.getHandlerFunction()))
    .forEach(t => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger('onSheetChange').forSpreadsheet(SpreadsheetApp.getActive()).onChange().create();
  // Backup daily run around 6:05 a.m. Jamaica (Apps Script allows about 15 minutes either side).
  ScriptApp.newTrigger('refreshBriefingFeed').timeBased().atHour(6).nearMinute(5).everyDays(1)
    .inTimezone('America/Jamaica').create();
}
