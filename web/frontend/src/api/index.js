import axios from 'axios'

const api = axios.create({ baseURL: '/api' })

export default {
  getDashboard: (date) => api.get('/dashboard', { params: { target_date: date } }),
  getActivities: (date) => api.get('/activities', { params: { target_date: date } }),
  getActivityDates: () => api.get('/activities/dates'),
  deleteActivity: (id) => api.delete(`/activities/${id}`),
  deleteActivitiesByDate: (date) => api.delete('/activities', { params: { target_date: date } }),
  getScreenshotUrl: (path) => `/api/activities/screenshot?path=${encodeURIComponent(path)}`,
  getWorklogs: (date) => api.get('/worklogs', { params: { date } }),
  updateDraft: (id, data) => api.patch(`/worklogs/${id}`, data),
  approveDraft: (id) => api.post(`/worklogs/${id}/approve`),
  rejectDraft: (id) => api.post(`/worklogs/${id}/reject`),
  approveAll: (date) => api.post('/worklogs/approve-all', null, { params: { date } }),
  submitDraft: (id) => api.post(`/worklogs/${id}/submit`),
  getAuditTrail: (id) => api.get(`/worklogs/${id}/audit`),
  getIssues: () => api.get('/issues'),
  addIssue: (data) => api.post('/issues', data),
  fetchJiraIssue: (key) => api.get(`/issues/fetch/${key}`),
  updateIssue: (key, data) => api.patch(`/issues/${key}`, data),
  deleteIssue: (key) => api.delete(`/issues/${key}`),
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
  search: (q, limit = 20, sourceType = null) => {
    const params = { q, limit }
    if (sourceType) params.source_type = sourceType
    return api.get('/search', { params })
  },
}
