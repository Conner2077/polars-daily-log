import axios from 'axios'

const api = axios.create({ baseURL: '/api' })

export default {
  getDashboard: (date, machineId = null) => api.get('/dashboard', { params: { target_date: date, ...(machineId && { machine_id: machineId }) } }),
  getActivities: (date, machineId = null) => api.get('/activities', { params: { target_date: date, ...(machineId && { machine_id: machineId }) } }),
  getActivityDates: (machineId = null) => api.get('/activities/dates', { params: machineId ? { machine_id: machineId } : {} }),
  getCollectors: () => api.get('/collectors'),
  deleteCollector: (id) => api.delete(`/collectors/${id}`),
  pauseCollector: (machineId) => api.post(`/collectors/${machineId}/pause`),
  resumeCollector: (machineId) => api.post(`/collectors/${machineId}/resume`),
  setCollectorConfig: (machineId, config) => api.put(`/collectors/${machineId}/config`, config),
  deleteActivity: (id) => api.delete(`/activities/${id}`),
  retryFailedActivities: (date) => api.post('/activities/retry-failed', null, { params: { target_date: date } }),
  deleteActivitiesByDate: (date) => api.delete('/activities', { params: { target_date: date } }),
  getRecycledActivities: () => api.get('/activities/recycle'),
  restoreActivities: (date) => api.post('/activities/recycle/restore', null, { params: { target_date: date } }),
  purgeActivities: (date) => api.delete('/activities/recycle/purge', { params: { target_date: date } }),
  purgeAllActivities: () => api.delete('/activities/recycle/purge-all'),
  getScreenshotUrl: (path) => `/api/activities/screenshot?path=${encodeURIComponent(path)}`,
  getWorklogs: (date) => api.get('/worklogs', { params: { date } }),
  updateDraft: (id, data) => api.patch(`/worklogs/${id}`, data),
  approveDraft: (id) => api.post(`/worklogs/${id}/approve`),
  rejectDraft: (id) => api.post(`/worklogs/${id}/reject`),
  deleteDraft: (id) => api.delete(`/worklogs/${id}`),
  approveAll: (date) => api.post('/worklogs/approve-all', null, { params: { date } }),
  submitDraft: (id) => api.post(`/worklogs/${id}/submit`),
  submitIssue: (id, index) => api.post(`/worklogs/${id}/submit-issue/${index}`),
  updateDraftIssue: (id, index, data) => api.patch(`/worklogs/${id}/issues/${index}`, data),
  getAuditTrail: (id) => api.get(`/worklogs/${id}/audit`),
  getIssues: () => api.get('/issues'),
  addIssue: (data) => api.post('/issues', data),
  fetchJiraIssue: (key) => api.get(`/issues/fetch/${key}`),
  updateIssue: (key, data) => api.patch(`/issues/${key}`, data),
  deleteIssue: (key) => api.delete(`/issues/${key}`),
  getJiraStatus: () => api.get('/settings/jira-status'),
  getSettings: () => api.get('/settings'),
  getDefaultPrompts: () => api.get('/settings/default-prompts'),
  getSetting: (key) => api.get(`/settings/${key}`),
  putSetting: (key, value) => api.put(`/settings/${key}`, { value }),
  checkLLMKey: (engine, apiKey, model = '', baseUrl = '') =>
    api.post('/settings/check-llm', { engine, api_key: apiKey, model, base_url: baseUrl }),
  jiraLogin: (mobile, password, jiraUrl) =>
    api.post('/settings/jira-login', { mobile, password, jira_url: jiraUrl }),
  jiraTest: (serverUrl, username, pat) =>
    api.post('/settings/jira-test', { server_url: serverUrl, username, pat }),
  jiraLoginGet: (mobile, password, jiraUrl) =>
    api.get('/settings/do-jira-login', { params: { mobile, password, jira_url: jiraUrl } }),
  getGitRepos: () => api.get('/git-repos'),
  addGitRepo: (data) => api.post('/git-repos', data),
  deleteGitRepo: (id) => api.delete(`/git-repos/${id}`),
  checkPeriodExists: (type, startDate = null, endDate = null) => {
    const data = { type }
    if (startDate) data.start_date = startDate
    if (endDate) data.end_date = endDate
    return api.post('/worklogs/check-exists', data)
  },
  generateSummary: (type, startDate = null, endDate = null, force = false) => {
    const data = { type, force }
    if (startDate) data.start_date = startDate
    if (endDate) data.end_date = endDate
    return api.post('/worklogs/generate', data)
  },
  getWorklogsByTag: (tag) => api.get('/worklogs', { params: { tag } }),
  submitFeedback: (type, content, page, userAgent) =>
    api.post('/feedback', { type, content, page, user_agent: userAgent }),
  search: (q, limit = 20, sourceType = null) => {
    const params = { q, limit }
    if (sourceType) params.source_type = sourceType
    return api.get('/search', { params })
  },
  // Summary types CRUD (legacy — kept for backward compat)
  getSummaryTypes: () => api.get('/summary-types'),
  createSummaryType: (data) => api.post('/summary-types', data),
  updateSummaryType: (name, data) => api.put(`/summary-types/${name}`, data),
  deleteSummaryType: (name) => api.delete(`/summary-types/${name}`),
  // Scopes + Outputs CRUD (new pipeline)
  getScopes: () => api.get('/scopes'),
  createScope: (data) => api.post('/scopes', data),
  updateScope: (name, data) => api.put(`/scopes/${name}`, data),
  deleteScope: (name) => api.delete(`/scopes/${name}`),
  getScopeOutputs: (scopeName) => api.get(`/scopes/${scopeName}/outputs`),
  createScopeOutput: (scopeName, data) => api.post(`/scopes/${scopeName}/outputs`, data),
  updateScopeOutput: (outputId, data) => api.put(`/scopes/outputs/${outputId}`, data),
  deleteScopeOutput: (outputId) => api.delete(`/scopes/outputs/${outputId}`),
  // Summaries (new pipeline)
  getSummaries: (params = {}) => api.get('/summaries', { params }),
  getSummary: (id) => api.get(`/summaries/${id}`),
  updateSummary: (id, data) => api.patch(`/summaries/${id}`, data),
  publishSummary: (id) => api.post(`/summaries/${id}/publish`),
  deleteSummary: (id) => api.delete(`/summaries/${id}`),
  getSummaryAudit: (id) => api.get(`/summaries/${id}/audit`),
  generateScopeSummary: (scopeName, targetDate = null, force = false) => {
    const data = { scope_name: scopeName, force }
    if (targetDate) data.target_date = targetDate
    return api.post('/summaries/generate', data)
  },
  // LLM engines CRUD
  getLLMEngines: () => api.get('/llm-engines'),
  createLLMEngine: (data) => api.post('/llm-engines', data),
  updateLLMEngine: (name, data) => api.put(`/llm-engines/${name}`, data),
  deleteLLMEngine: (name) => api.delete(`/llm-engines/${name}`),
  checkLLMEngine: (name) => api.post(`/llm-engines/${name}/check`),
  exportLLMEngines: () => api.get('/llm-engines/export'),
  importLLMEngines: (data) => api.post('/llm-engines/import', data),
  // Scheduler runs
  getSchedulerRuns: (params = {}) => api.get('/scheduler/runs', { params }),
  // Self-update endpoints — driven by the Settings → 自动更新 tab
  // and the global "new version available" banner in App.vue.
  checkForUpdate: (force = false) => api.get('/updates/check', { params: { force } }),
  getUpdateStatus: () => api.get('/updates/status'),
  installUpdate: (payload = {}) => api.post('/updates/install', payload),
  listBackups: () => api.get('/updates/backups'),
  rollbackUpdate: (backupId) => api.post('/updates/rollback', { backup_id: backupId }),
  pruneBackups: (keep = 3) => api.post('/updates/prune', { keep }),
}
