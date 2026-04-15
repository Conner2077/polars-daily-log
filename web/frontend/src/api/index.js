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
}
