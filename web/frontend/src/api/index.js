import axios from 'axios'

const api = axios.create({ baseURL: '/api' })

export default {
  getDashboard: (date) => api.get('/dashboard', { params: { target_date: date } }),
  getActivities: (date) => api.get('/activities', { params: { target_date: date } }),
  getWorklogs: (date) => api.get('/worklogs', { params: { date } }),
  updateDraft: (id, data) => api.patch(`/worklogs/${id}`, data),
  approveDraft: (id) => api.post(`/worklogs/${id}/approve`),
  rejectDraft: (id) => api.post(`/worklogs/${id}/reject`),
  approveAll: (date) => api.post('/worklogs/approve-all', null, { params: { date } }),
  submitDraft: (id) => api.post(`/worklogs/${id}/submit`),
  getAuditTrail: (id) => api.get(`/worklogs/${id}/audit`),
  getIssues: () => api.get('/issues'),
  addIssue: (data) => api.post('/issues', data),
  updateIssue: (key, data) => api.patch(`/issues/${key}`, data),
  deleteIssue: (key) => api.delete(`/issues/${key}`),
  getSettings: () => api.get('/settings'),
  getSetting: (key) => api.get(`/settings/${key}`),
  putSetting: (key, value) => api.put(`/settings/${key}`, { value }),
  getGitRepos: () => api.get('/git-repos'),
  addGitRepo: (data) => api.post('/git-repos', data),
  deleteGitRepo: (id) => api.delete(`/git-repos/${id}`),
}
