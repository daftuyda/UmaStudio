import { $, getJson, post } from '/static/ui.js';

export function initSettings(canSwitch) {
  const dialog = $('#settings');
  let snapshot, selected, drafts = {}, applying = false;
  const saveDraft = () => {
    if (selected) drafts[selected] = { game: $('#settingsGame').value, data: $('#settingsData').value };
  };
  function showRegion() {
    selected = $('#settingsRegion').value;
    const profile = snapshot.profiles[selected];
    const draft = drafts[selected] || { game: profile.gameOverride, data: profile.dataOverride };
    // The previous single-folder setting may be either an install or data directory.
    if (!draft.game && !draft.data && profile.path) {
      draft.game = profile.game || '';
      draft.data = profile.data || '';
    }
    $('#settingsGame').value = draft.game;
    $('#settingsData').value = draft.data;
    $('#settingsGameResolved').textContent = `Current location: ${profile.game || 'Not found — choose the install folder'}`;
    $('#settingsDataResolved').textContent = `Current location: ${profile.data || 'Not found'}${profile.found ? '' : ' (data not found)'}`;
    $('#settingsError').textContent = '';
  }
  async function open() {
    $('#settingsError').textContent = 'Loading settings…';
    $('#settingsApply').disabled = true;
    dialog.showModal();
    try {
      snapshot = await getJson('/api/settings', null);
      drafts = {};
      selected = null;
      $('#settingsRegion').value = snapshot.region;
      $('#settingsGameBrowse').hidden = $('#settingsDataBrowse').hidden = !snapshot.browse;
      showRegion();
      $('#settingsApply').disabled = false;
      $('#settingsRegion').focus();
    } catch (e) { $('#settingsError').textContent = e.message; }
  }
  $('#settingsBtn').onclick = open;
  $('#setupSettings').onclick = open;
  $('#settingsClose').onclick = () => { if (!applying) dialog.close(); };
  dialog.addEventListener('cancel', e => { if (applying) e.preventDefault(); });
  dialog.addEventListener('keydown', e => e.stopPropagation());
  $('#settingsRegion').onchange = () => { saveDraft(); showRegion(); };
  $('#settingsAuto').onclick = () => {
    $('#settingsGame').value = '';
    $('#settingsData').value = '';
    $('#settingsError').textContent = '';
  };
  for (const field of ['Game', 'Data']) {
    $(`#settings${field}Browse`).onclick = async () => {
      try {
        const result = await post('/api/game/browse', {}, null);
        if (result.path) $(`#settings${field}`).value = result.path;
      } catch (e) { $('#settingsError').textContent = e.message; }
    };
  }
  $('#settingsApply').onclick = async () => {
    if (!canSwitch()) {
      $('#settingsError').textContent = 'Finish or cancel the current export before switching games.';
      return;
    }
    applying = true;
    dialog.querySelectorAll('button, input, select').forEach(el => { el.disabled = true; });
    $('#settingsError').textContent = 'Switching game files…';
    try {
      await post('/api/settings', { region: selected, game: $('#settingsGame').value, data: $('#settingsData').value }, null);
      location.reload();
    } catch (e) {
      $('#settingsError').textContent = e.message;
      applying = false;
      dialog.querySelectorAll('button, input, select').forEach(el => { el.disabled = false; });
    }
  };
}
