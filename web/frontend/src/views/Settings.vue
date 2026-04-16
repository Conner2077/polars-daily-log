<template>
  <div class="settings-page">
    <!-- Page header -->
    <div class="page-header">
      <div class="page-header-left">
        <h2 class="page-title">设置</h2>
        <div class="page-subtitle">配置采集、LLM、Jira 等运行参数</div>
      </div>
      <div class="page-header-right">
        <el-button type="primary" round @click="saveAll">保存所有</el-button>
      </div>
    </div>

    <!-- Tab Navigation -->
    <div class="tab-nav">
      <button
        v-for="tab in tabs"
        :key="tab.name"
        :class="['tab-btn', { active: activeTab === tab.name }]"
        @click="activeTab = tab.name"
      >
        {{ tab.label }}
      </button>
    </div>

    <!-- Profile Tab -->
    <div v-show="activeTab === 'profile'" class="tab-content">
      <div class="settings-card">
        <h3 class="card-title">个人资料</h3>
        <p class="card-description">设置侧边栏展示的昵称。未设置时默认使用 Jira 显示名。</p>
        <el-form label-position="top" class="settings-form two-col">
          <el-form-item label="昵称">
            <el-input v-model="settings.user_nickname" placeholder="留空则使用 Jira 显示名" maxlength="40" show-word-limit />
            <div class="form-hint">只在本机侧边栏显示，不会同步到 Jira</div>
          </el-form-item>
        </el-form>
      </div>
    </div>

    <!-- Monitor Tab -->
    <div v-show="activeTab === 'monitor'" class="tab-content">
      <div class="settings-card">
        <h3 class="card-title">活动采集</h3>
        <p class="card-description">监控前台活动、OCR 识别与截图保留策略。</p>
        <el-form label-position="top" class="settings-form two-col">
          <el-form-item label="采样间隔（秒）">
            <el-input-number v-model="settings.monitor_interval_sec" :min="10" :max="300" />
            <div class="form-hint">每隔多少秒采集一次前台窗口</div>
          </el-form-item>
          <el-form-item label="OCR 引擎">
            <el-select v-model="settings.monitor_ocr_engine" style="width: 100%">
              <el-option label="自动" value="auto" />
              <el-option label="Vision (macOS)" value="vision" />
              <el-option label="WinOCR (Windows)" value="winocr" />
              <el-option label="Tesseract" value="tesseract" />
            </el-select>
            <div class="form-hint">留 auto 让系统按平台选择</div>
          </el-form-item>
          <el-form-item label="截图保留天数">
            <el-input-number v-model="settings.monitor_screenshot_retention_days" :min="1" :max="90" />
            <div class="form-hint">超过后自动清理原始截图</div>
          </el-form-item>
        </el-form>

        <div class="switch-group">
          <div class="switch-row">
            <el-switch v-model="settings.monitor_ocr_enabled" />
            <div class="switch-meta">
              <div class="switch-label">启用 OCR</div>
              <div class="switch-hint">对截图做文字识别，作为活动内容的补充</div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Git Repos Tab -->
    <div v-show="activeTab === 'git'" class="tab-content">
      <div class="settings-card">
        <h3 class="card-title">Git 仓库</h3>
        <p class="card-description">配置要追踪提交记录的 Git 仓库路径。</p>

        <el-table :data="gitRepos" style="margin-bottom: 20px" empty-text="还没有配置仓库">
          <el-table-column prop="path" label="路径" />
          <el-table-column prop="author_email" label="作者邮箱" />
          <el-table-column label="" width="80" align="center">
            <template #default="{ row }">
              <el-button text type="danger" @click="removeRepo(row.id)">
                <el-icon><Delete /></el-icon>
              </el-button>
            </template>
          </el-table-column>
        </el-table>

        <div class="add-repo-form">
          <el-input v-model="newRepo.path" placeholder="/path/to/repo" class="repo-input" />
          <el-input v-model="newRepo.author_email" placeholder="email@example.com" class="repo-input" />
          <el-button type="primary" round @click="addRepo">添加</el-button>
        </div>
      </div>
    </div>

    <!-- Jira Tab -->
    <div v-show="activeTab === 'jira'" class="tab-content">
      <div v-if="isLocalhost" class="alert-banner warning">
        <div class="alert-title">检测到通过 localhost 访问</div>
        <div class="alert-body">
          如果登录失败（只拿到 1 个 cookie），可能是本地代理（如 Clash）拦截了 localhost 请求。
          请改用
          <a :href="ipUrl">http://127.0.0.1:{{ windowPort }}</a>
          访问，可绕过代理。
        </div>
      </div>

      <div class="settings-card">
        <h3 class="card-title">Jira 连接</h3>
        <p class="card-description">配置 Jira 服务器地址与认证方式。</p>
        <el-form label-position="top" class="settings-form two-col">
          <el-form-item label="Server URL">
            <el-input v-model="settings.jira_server_url" placeholder="https://jira.example.com" />
            <div class="form-hint url-hint">Jira 根地址，不要带路径</div>
          </el-form-item>
          <el-form-item label="认证方式">
            <el-select v-model="settings.jira_auth_mode" style="width: 100%">
              <el-option label="SSO 自动登录（推荐）" value="cookie" />
              <el-option label="Bearer Token（PAT）" value="bearer" />
            </el-select>
          </el-form-item>
          <el-form-item v-if="settings.jira_auth_mode === 'bearer'" label="Personal Access Token" class="full-col">
            <el-input v-model="settings.jira_pat" type="password" show-password />
          </el-form-item>
        </el-form>
      </div>

      <div v-if="settings.jira_auth_mode === 'cookie'" class="settings-card" style="margin-top: 16px">
        <div class="card-head-row">
          <h3 class="card-title">SSO 登录</h3>
          <span v-if="jiraLoginResult" :class="['status-pill', jiraLoginResult.success ? 'success' : 'danger']">
            {{ jiraLoginResult.success ? '已登录' : '登录失败' }}
          </span>
        </div>
        <p class="card-description">
          输入帆软账号自动登录 Jira，获取并保存 Cookie。Cookie 过期后重新登录即可。
        </p>
        <el-form label-position="top" class="settings-form two-col" autocomplete="on">
          <el-form-item label="手机号">
            <el-input v-model="jiraLogin.mobile" placeholder="18800000000" name="username" autocomplete="username" />
          </el-form-item>
          <el-form-item label="密码">
            <el-input v-model="jiraLogin.password" type="password" show-password placeholder="帆软账号密码" name="password" autocomplete="current-password" />
          </el-form-item>
          <el-form-item class="full-col">
            <el-button type="primary" round :loading="jiraLogging" @click="doJiraLogin">
              {{ jiraLogging ? '登录中...' : '登录并获取 Cookie' }}
            </el-button>
            <span v-if="jiraLoginResult" class="inline-status" :class="{ success: jiraLoginResult.success, danger: !jiraLoginResult.success }">
              {{ jiraLoginResult.message }}
            </span>
          </el-form-item>
        </el-form>
      </div>
    </div>

    <!-- LLM Tab -->
    <div v-show="activeTab === 'llm'" class="tab-content">
      <div class="settings-card">
        <h3 class="card-title">LLM 引擎</h3>
        <p class="card-description">用于每日总结、自动审批与活动摘要的模型。</p>

        <!-- Quick fill buttons -->
        <div class="quick-fill-row">
          <span class="quick-fill-label">快速填充</span>
          <el-button
            v-for="p in presets" :key="p.name"
            size="small" round
            @click="applyPreset(p)"
          >{{ p.name }}</el-button>
        </div>

        <el-form label-position="top" class="settings-form two-col">
          <el-form-item label="API 协议">
            <el-select v-model="settings.llm_engine" style="width: 100%">
              <el-option label="OpenAI 兼容（OpenAI / Kimi / DeepSeek / 智谱 / …）" value="openai_compat" />
              <el-option label="Anthropic（Claude）" value="anthropic" />
              <el-option label="Ollama 本地" value="ollama" />
            </el-select>
          </el-form-item>
          <el-form-item label="Model">
            <el-input v-model="settings.llm_model" :placeholder="modelPlaceholder" />
          </el-form-item>
          <el-form-item label="API Key" class="full-col">
            <el-input v-model="settings.llm_api_key" type="password" show-password placeholder="留空则使用安装时配置的内置模型（若有）" />
          </el-form-item>
          <el-form-item label="Base URL" class="full-col">
            <el-input v-model="settings.llm_base_url" :placeholder="basePlaceholder" />
            <div class="form-hint">
              填<strong>根地址</strong>，不要带 <code>/chat/completions</code> 等接口路径。留空用默认：
              <code>{{ basePlaceholder }}</code>
            </div>
          </el-form-item>
          <el-form-item class="full-col">
            <el-button round :loading="checkingKey" @click="checkLLMKey">
              {{ checkingKey ? '测试中...' : '测试连接' }}
            </el-button>
            <span v-if="keyCheckResult" class="inline-status" :class="{ success: keyCheckResult.valid, danger: !keyCheckResult.valid }">
              {{ keyCheckResult.message }}
            </span>
          </el-form-item>
        </el-form>
      </div>
    </div>

    <!-- Prompts Tab -->
    <div v-show="activeTab === 'prompts'" class="tab-content">
      <div class="settings-card">
        <h3 class="card-title">Prompt 模板</h3>
        <p class="card-description">
          控制 LLM 生成工作日志的行为。留空则使用系统默认模板。修改后点击顶部「保存所有」按钮生效。
        </p>

        <div class="prompt-section">
          <div class="prompt-header">
            <div>
              <h4 class="prompt-title">每日日志生成 Prompt</h4>
              <p class="prompt-desc">每天定时触发时，为每个相关 Jira 任务生成一条工作日志草稿。</p>
            </div>
            <el-button size="small" link class="reset-btn" @click="resetPrompt('summarize_prompt')">恢复默认</el-button>
          </div>
          <p class="prompt-vars">
            可用变量：<code>{date}</code> <code>{jira_issues}</code> <code>{git_commits}</code> <code>{activities}</code>
          </p>
          <el-input
            v-model="settings.summarize_prompt"
            type="textarea"
            :rows="14"
            resize="vertical"
            class="prompt-textarea"
          />
        </div>

        <div class="prompt-section">
          <div class="prompt-header">
            <div>
              <h4 class="prompt-title">自动审批 Prompt</h4>
              <p class="prompt-desc">在自动审批时间评估未审批的草稿，合格则自动提交到 Jira。</p>
            </div>
            <el-button size="small" link class="reset-btn" @click="resetPrompt('auto_approve_prompt')">恢复默认</el-button>
          </div>
          <p class="prompt-vars">
            可用变量：<code>{date}</code> <code>{issue_key}</code> <code>{issue_summary}</code> <code>{time_spent_hours}</code> <code>{summary}</code> <code>{git_commits}</code>
          </p>
          <el-input
            v-model="settings.auto_approve_prompt"
            type="textarea"
            :rows="14"
            resize="vertical"
            class="prompt-textarea"
          />
        </div>

        <div class="prompt-section">
          <div class="prompt-header">
            <div>
              <h4 class="prompt-title">周报 / 月报生成 Prompt</h4>
              <p class="prompt-desc">手动触发时，聚合该周期内所有每日日志生成总结。</p>
            </div>
            <el-button size="small" link class="reset-btn" @click="resetPrompt('period_summary_prompt')">恢复默认</el-button>
          </div>
          <p class="prompt-vars">
            可用变量：<code>{period_start}</code> <code>{period_end}</code> <code>{period_type}</code> <code>{daily_logs}</code>
          </p>
          <el-input
            v-model="settings.period_summary_prompt"
            type="textarea"
            :rows="14"
            resize="vertical"
            class="prompt-textarea"
          />
        </div>

        <div class="prompt-section">
          <div class="prompt-header">
            <div>
              <h4 class="prompt-title">活动内容猜测 Prompt</h4>
              <p class="prompt-desc">后台 worker 为每条活动生成一段 ≤100 字的「此刻在做什么」摘要。</p>
            </div>
            <el-button size="small" link class="reset-btn" @click="resetPrompt('activity_summary_prompt')">恢复默认</el-button>
          </div>
          <p class="prompt-vars">
            可用变量：<code>{prev_summaries}</code> <code>{timestamp}</code> <code>{app_name}</code> <code>{window_title}</code> <code>{url}</code> <code>{tab_title}</code> <code>{ocr_text}</code> <code>{wecom_group}</code>
          </p>
          <el-input
            v-model="settings.activity_summary_prompt"
            type="textarea"
            :rows="14"
            resize="vertical"
            class="prompt-textarea"
          />
        </div>
      </div>
    </div>

    <!-- Scheduler Tab -->
    <div v-show="activeTab === 'scheduler'" class="tab-content">
      <div class="settings-card">
        <h3 class="card-title">定时任务</h3>
        <p class="card-description">控制每日生成与自动审批的触发时间。</p>

        <div class="switch-group">
          <div class="switch-row">
            <el-switch v-model="settings.scheduler_enabled" />
            <div class="switch-meta">
              <div class="switch-label">启用每日自动生成</div>
              <div class="switch-hint">到点后自动汇总活动与 Git 记录生成日志草稿</div>
            </div>
          </div>
          <div class="switch-row">
            <el-switch v-model="settings.auto_approve_enabled" />
            <div class="switch-meta">
              <div class="switch-label">启用自动审批</div>
              <div class="switch-hint">到点后评估未审批草稿，合格则自动提交到 Jira</div>
            </div>
          </div>
        </div>

        <el-form label-position="top" class="settings-form two-col" style="margin-top: 24px">
          <el-form-item label="每日日志生成时间">
            <el-time-picker v-model="settings.scheduler_trigger_time" format="HH:mm" value-format="HH:mm" />
          </el-form-item>
          <el-form-item label="自动审批并提交时间">
            <el-time-picker v-model="settings.auto_approve_trigger_time" format="HH:mm" value-format="HH:mm" />
          </el-form-item>
        </el-form>
      </div>
    </div>

    <!-- Collectors Tab -->
    <div v-show="activeTab === 'collectors'" class="tab-content">
      <div class="settings-card">
        <div class="card-head-row">
          <div>
            <h3 class="card-title">数据采集节点</h3>
            <p class="card-description">
              列出所有已注册的 collector。本机自带的使用 machine_id = <code>local</code>。
              远程 collector 通过 <code>python -m auto_daily_log_collector</code> 启动。
            </p>
          </div>
          <el-button size="small" round @click="loadCollectors">
            <el-icon><Refresh /></el-icon>&nbsp;刷新
          </el-button>
        </div>

        <el-empty v-if="collectors.length === 0" description="还没有采集节点，首次启动 collector 会自动出现在这里" />
        <el-table v-else :data="collectors" style="width: 100%">
          <el-table-column label="名称" min-width="200">
            <template #default="{ row }">
              <div class="collector-name">
                <span
                  class="status-dot"
                  :class="row.is_paused ? 'paused' : (isOnline(row) ? 'online' : 'offline')"
                />
                <span class="platform-icon">{{ platformIcon(row.platform) }}</span>
                <strong>{{ row.name }}</strong>
                <span v-if="row.hostname" class="hostname">({{ row.hostname }})</span>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="平台" min-width="140">
            <template #default="{ row }">
              <span class="cell-muted">
                {{ row.platform_detail || row.platform || '—' }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="能力" min-width="200">
            <template #default="{ row }">
              <el-tag
                v-for="cap in row.capabilities" :key="cap"
                size="small"
                round
                class="cap-pill"
              >{{ cap }}</el-tag>
              <span v-if="!row.capabilities || row.capabilities.length === 0" class="cell-muted">—</span>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="100">
            <template #default="{ row }">
              <span v-if="row.is_paused" class="status-pill muted">已暂停</span>
              <span v-else-if="isOnline(row)" class="status-pill success">在线</span>
              <span v-else class="status-pill muted">离线</span>
            </template>
          </el-table-column>
          <el-table-column label="最后心跳" min-width="170">
            <template #default="{ row }">
              <span class="cell-mono">
                {{ row.last_seen || '—' }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="200">
            <template #default="{ row }">
              <el-button
                v-if="row.is_paused"
                size="small" round
                @click="resumeCol(row)"
              >继续</el-button>
              <el-button
                v-else
                size="small" round
                @click="pauseCol(row)"
              >暂停</el-button>
              <el-popconfirm
                :title="`移除 ${row.name}？不会删除历史数据`"
                confirm-button-text="移除"
                cancel-button-text="取消"
                :width="260"
                @confirm="removeCol(row)"
              >
                <template #reference>
                  <el-button size="small" round class="danger-btn">移除</el-button>
                </template>
              </el-popconfirm>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </div>

    <!-- Recycle Bin Tab -->
    <div v-show="activeTab === 'recycle'" class="tab-content">
      <div class="settings-card">
        <h3 class="card-title">回收站</h3>
        <p class="card-description">
          被删除的活动记录会保留在回收站中，超过 {{ settings.recycle_retention_days || 30 }} 天后自动永久删除。
        </p>
        <el-form label-position="top" class="settings-form two-col" style="margin-bottom: 20px">
          <el-form-item label="活动记录保留天数">
            <el-input-number v-model="settings.activity_retention_days" :min="1" :max="365" />
            <div class="form-hint">超过此天数自动移入回收站</div>
          </el-form-item>
          <el-form-item label="回收站保留天数">
            <el-input-number v-model="settings.recycle_retention_days" :min="1" :max="365" />
            <div class="form-hint">超过此天数自动永久删除</div>
          </el-form-item>
        </el-form>

        <el-empty v-if="recycledItems.length === 0" description="回收站为空" />
        <el-table v-else :data="recycledItems" style="width: 100%">
          <el-table-column prop="date" label="日期" width="140" />
          <el-table-column prop="count" label="记录数" width="100" />
          <el-table-column prop="deleted_at" label="删除时间" width="200">
            <template #default="{ row }">
              <span class="cell-mono">{{ row.deleted_at?.substring(0, 16) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="220">
            <template #default="{ row }">
              <el-button size="small" round @click="restoreDate(row.date)">恢复</el-button>
              <el-popconfirm
                title="永久删除后不可恢复，确认继续？"
                confirm-button-text="永久删除"
                cancel-button-text="取消"
                :width="280"
                @confirm="purgeDate(row.date)"
              >
                <template #reference>
                  <el-button size="small" round class="danger-btn">永久删除</el-button>
                </template>
              </el-popconfirm>
            </template>
          </el-table-column>
        </el-table>

        <div v-if="recycledItems.length > 0" style="margin-top: 16px; display: flex; justify-content: flex-end">
          <el-popconfirm
            title="清空回收站后所有记录将永久删除，确认继续？"
            confirm-button-text="清空"
            cancel-button-text="取消"
            :width="300"
            @confirm="purgeAll"
          >
            <template #reference>
              <el-button round class="danger-btn-strong">清空回收站</el-button>
            </template>
          </el-popconfirm>
        </div>
      </div>
    </div>

    <!-- Updates Tab -->
    <div v-show="activeTab === 'updates'" class="tab-content">
      <div class="settings-card">
        <h3 class="card-title">自动更新</h3>
        <p class="card-description">
          检测自动进行（每 24 小时缓存一次），实际升级由你手动触发。升级前会自动备份数据库与配置。
        </p>

        <div class="update-summary">
          <div class="update-version-row">
            <div>
              <div class="form-hint">当前版本</div>
              <div class="cell-mono">{{ updateInfo.current || '—' }}</div>
            </div>
            <div>
              <div class="form-hint">最新版本</div>
              <div class="cell-mono">{{ updateInfo.latest || '—' }}</div>
            </div>
            <div>
              <div class="form-hint">状态</div>
              <div>
                <el-tag v-if="updateInfo.available" type="warning">有新版本</el-tag>
                <el-tag v-else-if="updateInfo.latest" type="success">已是最新</el-tag>
                <el-tag v-else type="info">未检测</el-tag>
              </div>
            </div>
          </div>

          <div style="margin-top: 16px; display: flex; gap: 8px">
            <el-button round @click="checkUpdate(true)" :loading="checkingUpdate">立即检查</el-button>
            <el-popconfirm
              title="升级期间服务会短暂下线（约 30 秒），浏览器会显示重连状态。确认继续？"
              confirm-button-text="开始升级"
              cancel-button-text="取消"
              :width="320"
              @confirm="installUpdate"
            >
              <template #reference>
                <el-button type="primary" round :disabled="!updateInfo.available || installing">
                  升级到 {{ updateInfo.latest }}
                </el-button>
              </template>
            </el-popconfirm>
          </div>

          <div v-if="updateInfo.notes" style="margin-top: 12px">
            <details>
              <summary style="cursor: pointer; font-size: 13px; color: #666">Release Notes</summary>
              <pre class="release-notes">{{ updateInfo.notes }}</pre>
            </details>
          </div>
        </div>

        <div v-if="updateStatus.phase && updateStatus.phase !== 'idle'" class="update-progress">
          <div class="form-hint">升级进度</div>
          <el-progress
            :percentage="updateStatus.progress_pct"
            :status="updateStatus.phase === 'failed' ? 'exception' : (updateStatus.phase === 'completed' ? 'success' : '')"
          />
          <div class="cell-mono" style="margin-top: 6px">
            [{{ updateStatus.phase }}] {{ updateStatus.message }}
          </div>
          <details v-if="updateStatus.log && updateStatus.log.length" style="margin-top: 8px">
            <summary style="cursor: pointer; font-size: 13px; color: #666">详细日志</summary>
            <pre class="release-notes">{{ updateStatus.log.join('\n') }}</pre>
          </details>
        </div>
      </div>

      <div class="settings-card" style="margin-top: 20px">
        <h3 class="card-title">备份与回滚</h3>
        <p class="card-description">
          每次升级前自动创建一次备份。需要回到老版本时，选一条备份执行回滚——会自动恢复数据库并重装老版本。
        </p>

        <el-empty v-if="backups.length === 0" description="还没有任何备份" />
        <el-table v-else :data="backups" style="width: 100%">
          <el-table-column prop="id" label="备份 ID" width="180">
            <template #default="{ row }">
              <span class="cell-mono">{{ row.id }}</span>
              <el-tag v-if="row.is_first_install" size="small" type="info" style="margin-left: 8px">首次安装</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="版本变化" width="200">
            <template #default="{ row }">
              <span class="cell-mono">{{ row.old_version }}</span> → <span class="cell-mono">{{ row.new_version }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="created_at" label="创建时间" width="220">
            <template #default="{ row }">
              <span class="cell-mono">{{ row.created_at?.substring(0, 19).replace('T', ' ') }}</span>
            </template>
          </el-table-column>
          <el-table-column label="DB 大小">
            <template #default="{ row }">{{ formatBytes(row.db_size_bytes) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="160">
            <template #default="{ row }">
              <el-popconfirm
                :title="`回滚到 ${row.old_version}？服务会短暂下线，数据库将被覆盖为该备份。`"
                confirm-button-text="回滚"
                cancel-button-text="取消"
                :width="320"
                @confirm="rollback(row.id)"
              >
                <template #reference>
                  <el-button size="small" round class="danger-btn">回滚到此</el-button>
                </template>
              </el-popconfirm>
            </template>
          </el-table-column>
        </el-table>

        <div v-if="backups.length > 3" style="margin-top: 16px; display: flex; justify-content: flex-end">
          <el-popconfirm
            title="保留最近 3 个备份与首次安装快照，其余删除。确认继续？"
            confirm-button-text="清理"
            cancel-button-text="取消"
            :width="300"
            @confirm="pruneBackups"
          >
            <template #reference>
              <el-button round class="danger-btn">清理旧备份</el-button>
            </template>
          </el-popconfirm>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh, Delete } from '@element-plus/icons-vue'
import api from '../api'

const activeTab = ref('profile')
const tabs = [
  { name: 'profile', label: '个人资料' },
  { name: 'monitor', label: '活动采集' },
  { name: 'git', label: 'Git 仓库' },
  { name: 'jira', label: 'Jira 连接' },
  { name: 'llm', label: 'LLM 引擎' },
  { name: 'prompts', label: 'Prompt 模板' },
  { name: 'scheduler', label: '定时任务' },
  { name: 'collectors', label: '数据采集节点' },
  { name: 'recycle', label: '回收站' },
  { name: 'updates', label: '自动更新' },
]

const isLocalhost = window.location.hostname === 'localhost'
const windowPort = window.location.port || '8888'
const ipUrl = `http://127.0.0.1:${windowPort}/${window.location.hash}`

const checkingKey = ref(false)
const keyCheckResult = ref(null)
const jiraLogin = ref({ mobile: '', password: '' })
const jiraLogging = ref(false)
const jiraLoginResult = ref(null)

const settings = ref({
  user_nickname: '',
  monitor_interval_sec: 30, monitor_ocr_enabled: true, monitor_ocr_engine: 'auto',
  monitor_screenshot_retention_days: 7, jira_server_url: '', jira_pat: '', jira_auth_mode: 'cookie', jira_cookie: '',
  llm_engine: 'openai_compat', llm_api_key: '', llm_model: '', llm_base_url: '',
  summarize_prompt: '', auto_approve_prompt: '', period_summary_prompt: '', activity_summary_prompt: '',
  scheduler_enabled: true, scheduler_trigger_time: '18:00',
  auto_approve_enabled: true, auto_approve_trigger_time: '21:30',
  activity_retention_days: 7, recycle_retention_days: 30,
})
const recycledItems = ref([])
const collectors = ref([])

// ─── Self-update state ────────────────────────────────────────────────
const updateInfo = ref({ current: '', latest: '', available: false, wheel_url: '', notes: '' })
const updateStatus = ref({ phase: 'idle', progress_pct: 0, message: '', log: [] })
const backups = ref([])
const checkingUpdate = ref(false)
const installing = ref(false)
let _updatePollTimer = null

function formatBytes(n) {
  if (!n || n < 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  let i = 0
  let v = n
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return `${v.toFixed(i === 0 ? 0 : 1)} ${units[i]}`
}

async function checkUpdate(force = false) {
  checkingUpdate.value = true
  try {
    const r = await api.checkForUpdate(force)
    updateInfo.value = r.data
    if (force) {
      ElMessage.success(updateInfo.value.available ? `发现新版本 ${updateInfo.value.latest}` : '已是最新版本')
    }
  } catch (e) {
    ElMessage.error(`检查失败: ${e?.message || e}`)
  } finally {
    checkingUpdate.value = false
  }
}

async function loadBackups() {
  try {
    const r = await api.listBackups()
    backups.value = r.data
  } catch (e) {
    // backups dir may not exist yet — that's fine
    backups.value = []
  }
}

async function pollUpdateStatus() {
  try {
    const r = await api.getUpdateStatus()
    updateStatus.value = r.data
    if (r.data.phase === 'completed' || r.data.phase === 'failed') {
      installing.value = false
      stopPollingStatus()
      await loadBackups()
      if (r.data.phase === 'completed') {
        ElMessage.success(`升级到 ${r.data.target_version} 完成`)
        // Refresh current version from API
        setTimeout(() => checkUpdate(true), 1500)
      } else {
        ElMessage.error(`升级失败: ${r.data.error || r.data.message}`)
      }
    }
  } catch (e) {
    // Server is restarting — keep polling
  }
}

function startPollingStatus() {
  stopPollingStatus()
  _updatePollTimer = setInterval(pollUpdateStatus, 1500)
}

function stopPollingStatus() {
  if (_updatePollTimer) { clearInterval(_updatePollTimer); _updatePollTimer = null }
}

async function installUpdate() {
  installing.value = true
  try {
    await api.installUpdate({})
    ElMessage.info('升级已开始，服务即将重启')
    startPollingStatus()
  } catch (e) {
    installing.value = false
    ElMessage.error(`启动升级失败: ${e?.response?.data?.detail || e?.message || e}`)
  }
}

async function rollback(backupId) {
  installing.value = true
  try {
    await api.rollbackUpdate(backupId)
    ElMessage.info(`回滚到备份 ${backupId} 已开始`)
    startPollingStatus()
  } catch (e) {
    installing.value = false
    ElMessage.error(`启动回滚失败: ${e?.response?.data?.detail || e?.message || e}`)
  }
}

async function pruneBackups() {
  try {
    const r = await api.pruneBackups(3)
    ElMessage.success(`清理了 ${r.data.removed.length} 个旧备份`)
    await loadBackups()
  } catch (e) {
    ElMessage.error(`清理失败: ${e?.message || e}`)
  }
}

// Preset shortcuts — pure UI, not persisted. Clicking a preset fills
// protocol + URL + model; user then only needs to paste their API Key.
const presets = [
  { name: 'Kimi',        engine: 'openai_compat', base_url: 'https://api.moonshot.cn/v1',   model: 'moonshot-v1-8k' },
  { name: 'OpenAI',      engine: 'openai_compat', base_url: 'https://api.openai.com/v1',    model: 'gpt-4o' },
  { name: 'DeepSeek',    engine: 'openai_compat', base_url: 'https://api.deepseek.com',     model: 'deepseek-chat' },
  { name: 'Claude',      engine: 'anthropic',     base_url: 'https://api.anthropic.com',    model: 'claude-sonnet-4-20250514' },
  { name: 'Ollama 本地', engine: 'ollama',        base_url: 'http://localhost:11434',       model: 'llama3' },
]

function applyPreset(p) {
  settings.value.llm_engine = p.engine
  settings.value.llm_base_url = p.base_url
  settings.value.llm_model = p.model
  ElMessage.success(`已填入 ${p.name} 预设，请补充 API Key`)
}

// Map protocol → default URL/model for placeholder hints
const PROTOCOL_DEFAULTS = {
  openai_compat: { url: 'https://api.moonshot.cn/v1', model: 'moonshot-v1-8k' },
  anthropic:     { url: 'https://api.anthropic.com',  model: 'claude-sonnet-4-20250514' },
  ollama:        { url: 'http://localhost:11434',     model: 'llama3' },
}
const basePlaceholder = computed(() => (PROTOCOL_DEFAULTS[settings.value.llm_engine] || {}).url || '')
const modelPlaceholder = computed(() => (PROTOCOL_DEFAULTS[settings.value.llm_engine] || {}).model || '')

function platformIcon(p) {
  if (!p) return '💻'
  if (p === 'macos') return '🖥'
  if (p === 'windows') return '🪟'
  if (p.startsWith('linux')) return '🐧'
  return '💻'
}

function isOnline(c) {
  if (!c.last_seen) return false
  const last = new Date(c.last_seen.replace(' ', 'T') + 'Z').getTime()
  return Date.now() - last < 3 * 60 * 1000
}

async function loadCollectors() {
  try {
    const r = await api.getCollectors()
    collectors.value = r.data
  } catch { /* ignore */ }
}

async function pauseCol(row) {
  await api.pauseCollector(row.machine_id)
  ElMessage.success(`${row.name} 已暂停`)
  await loadCollectors()
}

async function resumeCol(row) {
  await api.resumeCollector(row.machine_id)
  ElMessage.success(`${row.name} 已继续`)
  await loadCollectors()
}

async function removeCol(row) {
  await api.deleteCollector(row.id)
  ElMessage.success(`${row.name} 已移除`)
  await loadCollectors()
}
const gitRepos = ref([])
const newRepo = ref({ path: '', author_email: '' })

const defaultPrompts = ref({
  summarize_prompt: '',
  auto_approve_prompt: '',
  period_summary_prompt: '',
  activity_summary_prompt: '',
})

async function loadSettings() {
  const res = await api.getSettings()
  for (const item of res.data) {
    if (item.key in settings.value) {
      const val = item.value
      if (val === 'true') settings.value[item.key] = true
      else if (val === 'false') settings.value[item.key] = false
      else if (!isNaN(Number(val)) && val !== '') settings.value[item.key] = Number(val)
      else settings.value[item.key] = val
    }
  }
}

async function loadGitRepos() {
  try { const res = await api.getGitRepos(); gitRepos.value = res.data } catch (e) { /* ignore */ }
}

async function addRepo() {
  if (!newRepo.value.path) return
  await api.addGitRepo(newRepo.value)
  newRepo.value = { path: '', author_email: '' }
  await loadGitRepos()
}

async function removeRepo(id) {
  await api.deleteGitRepo(id)
  await loadGitRepos()
}

async function doJiraLogin() {
  if (!jiraLogin.value.mobile || !jiraLogin.value.password) {
    jiraLoginResult.value = { success: false, message: '请输入手机号和密码' }
    return
  }
  jiraLogging.value = true
  jiraLoginResult.value = null
  try {
    const res = await api.jiraLoginGet(
      jiraLogin.value.mobile,
      jiraLogin.value.password,
      settings.value.jira_server_url || 'https://work.fineres.com/'
    )
    const d = res.data
    jiraLoginResult.value = {
      success: d.success,
      message: d.success ? `当前登录为：${d.username || '未知用户'}` : (d.message || '登录失败')
    }
    if (res.data.success) {
      jiraLogin.value.password = ''
      await loadSettings()
    }
  } catch (e) {
    jiraLoginResult.value = { success: false, message: e.response?.data?.detail || 'Login failed' }
  } finally {
    jiraLogging.value = false
  }
}

async function checkLLMKey() {
  if (!settings.value.llm_api_key && settings.value.llm_engine !== 'ollama') {
    keyCheckResult.value = { valid: false, message: 'Please enter an API Key first' }
    return
  }
  checkingKey.value = true
  keyCheckResult.value = null
  try {
    const res = await api.checkLLMKey(
      settings.value.llm_engine,
      settings.value.llm_api_key,
      settings.value.llm_model,
      settings.value.llm_base_url,
    )
    keyCheckResult.value = res.data
  } catch (e) {
    keyCheckResult.value = { valid: false, message: e.response?.data?.detail || 'Check failed' }
  } finally {
    checkingKey.value = false
  }
}

async function loadDefaultPrompts() {
  try {
    const res = await api.getDefaultPrompts()
    defaultPrompts.value = res.data
    // If the stored setting is empty (meaning "use default"), prefill the
    // textarea with the default so users can edit inline.
    for (const key of ['summarize_prompt', 'auto_approve_prompt', 'period_summary_prompt', 'activity_summary_prompt']) {
      if (!settings.value[key] || settings.value[key].trim() === '') {
        settings.value[key] = defaultPrompts.value[key] || ''
      }
    }
  } catch (e) { /* ignore */ }
}

function isDefaultPrompt(key) {
  const cur = (settings.value[key] || '').trim()
  const def = (defaultPrompts.value[key] || '').trim()
  return cur === def
}

function resetPrompt(key) {
  settings.value[key] = defaultPrompts.value[key] || ''
}

async function saveAll() {
  const PROMPT_KEYS = new Set(['summarize_prompt', 'auto_approve_prompt', 'period_summary_prompt', 'activity_summary_prompt'])
  for (const [key, value] of Object.entries(settings.value)) {
    let out = value
    // Prompts: if user didn't change the default, save as empty string so
    // that future default-template updates propagate automatically.
    if (PROMPT_KEYS.has(key) && isDefaultPrompt(key)) {
      out = ''
    }
    await api.putSetting(key, String(out))
  }
  ElMessage.success('设置已保存')
}

async function loadRecycled() {
  try { const res = await api.getRecycledActivities(); recycledItems.value = res.data } catch {}
}

async function restoreDate(date) {
  await api.restoreActivities(date)
  ElMessage.success(`${date} 的记录已恢复`)
  await loadRecycled()
}

async function purgeDate(date) {
  await api.purgeActivities(date)
  ElMessage.success(`${date} 的记录已永久删除`)
  await loadRecycled()
}

async function purgeAll() {
  await api.purgeAllActivities()
  ElMessage.success('回收站已清空')
  recycledItems.value = []
}

onMounted(async () => {
  // settings must load BEFORE defaults: defaults prefill only when setting is empty
  await loadSettings()
  await loadDefaultPrompts()
  loadGitRepos()
  loadRecycled()
  loadCollectors()
  // Updates: cached check is cheap; backups list is local file scan.
  checkUpdate(false)
  loadBackups()
  // If the page is reloaded mid-upgrade, resume polling so the bar comes back.
  pollUpdateStatus().then(() => {
    if (['starting', 'stopping_server', 'backing_up', 'downloading',
         'installing', 'migrating', 'restarting'].includes(updateStatus.value.phase)) {
      installing.value = true
      startPollingStatus()
    }
  })
})

onUnmounted(() => stopPollingStatus())
</script>

<style scoped>
.settings-page {
  width: 100%;
  max-width: 960px;
  margin: 0 auto;
}

/* ───── Page header ───── */
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 24px;
  gap: 16px;
}

.page-header-left {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.page-title {
  font-size: 22px;
  font-weight: 700;
  letter-spacing: -0.5px;
  color: var(--ink);
  margin: 0;
  line-height: 1.2;
}

.page-subtitle {
  font-size: 13px;
  color: var(--ink-muted);
}

/* ───── Tab nav (ink underline, no pills) ───── */
.tab-nav {
  display: flex;
  gap: 24px;
  margin-bottom: 24px;
  border-bottom: 1px solid var(--line);
  overflow-x: auto;
  /* Hide the scrollbar chrome — macOS renders a permanent track otherwise.
     Horizontal scroll still works via trackpad / wheel. */
  scrollbar-width: none;           /* Firefox */
  -ms-overflow-style: none;        /* IE/Edge legacy */
}
.tab-nav::-webkit-scrollbar {
  display: none;                   /* Chrome / Safari */
}

.tab-btn {
  position: relative;
  padding: 10px 0;
  border: none;
  background: transparent;
  font-size: 14px;
  font-weight: 400;
  color: var(--ink-muted);
  cursor: pointer;
  font-family: var(--font);
  white-space: nowrap;
  transition: color 0.15s ease;
}

.tab-btn:hover {
  color: var(--ink);
}

.tab-btn.active {
  color: var(--ink);
  font-weight: 500;
}

.tab-btn.active::after {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  bottom: -1px;
  height: 2px;
  background: var(--ink);
}

.tab-content {
  animation: fadeIn 0.2s ease;
}

@keyframes fadeIn {
  from { opacity: 0; transform: translateY(4px); }
  to { opacity: 1; transform: translateY(0); }
}

/* ───── Card chrome ───── */
.settings-card {
  background: var(--bg);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 24px;
  box-shadow: none;
}

.settings-card + .settings-card {
  margin-top: 16px;
}

.card-head-row {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 4px;
}

.card-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--ink);
  margin: 0 0 4px 0;
  letter-spacing: -0.2px;
}

.card-description {
  font-size: 13px;
  color: var(--ink-muted);
  margin: 0 0 20px 0;
  line-height: 1.5;
}

.card-description code {
  background: var(--bg-soft);
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 12px;
  font-family: var(--font-mono);
  color: var(--ink-soft);
}

/* ───── Forms ───── */
.settings-form {
  margin-top: 4px;
}

.settings-form.two-col {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 0 20px;
}

.settings-form.two-col :deep(.el-form-item.full-col) {
  grid-column: 1 / -1;
}

.settings-form :deep(.el-form-item) {
  margin-bottom: 20px;
}

.settings-form :deep(.el-form-item__label) {
  font-size: 13px;
  font-weight: 500;
  color: var(--ink-soft);
  padding-bottom: 6px;
  line-height: 1.4;
}

/* Ensure el-input-number and el-time-picker take full width in grid cells */
.settings-form :deep(.el-input-number),
.settings-form :deep(.el-date-editor) {
  width: 100%;
}

/* Unify heights across el-select and el-input so side-by-side fields line up
   in the two-col grid. el-select__wrapper defaults to min-height: 36px which
   is visibly taller than el-input__wrapper (32px). Pin both to 34px with
   matching vertical padding so fields align. */
.settings-form :deep(.el-input__wrapper),
.settings-form :deep(.el-select__wrapper) {
  min-height: 34px;
  padding: 1px 11px;
  box-sizing: border-box;
  line-height: 1.4;
}
.settings-form :deep(.el-select__wrapper .el-select__selection) {
  min-height: 0;
}

.form-hint {
  font-size: 12px;
  color: var(--ink-muted);
  margin-top: 6px;
  line-height: 1.5;
}

.form-hint code {
  background: var(--bg-soft);
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 11px;
  font-family: var(--font-mono);
  color: var(--ink-soft);
}

.form-hint strong {
  color: var(--ink);
}

.url-hint {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.inline-status {
  margin-left: 12px;
  font-size: 13px;
}

.inline-status.success { color: var(--success); }
.inline-status.danger { color: var(--danger); }

/* ───── Switch groups ───── */
.switch-group {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-top: 8px;
}

.switch-row {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 12px 0;
}

.switch-row + .switch-row {
  border-top: 1px solid var(--line-soft);
}

.switch-meta {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.switch-label {
  font-size: 14px;
  font-weight: 500;
  color: var(--ink);
}

.switch-hint {
  font-size: 12px;
  color: var(--ink-muted);
  line-height: 1.4;
}

/* ───── Git repo add form ───── */
.add-repo-form {
  display: flex;
  gap: 10px;
  align-items: flex-start;
}

.repo-input {
  flex: 1;
}

/* ───── LLM quick-fill row ───── */
.quick-fill-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin: 0 0 20px;
  padding: 12px 16px;
  background: var(--bg-soft);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
}

.quick-fill-label {
  font-size: 12px;
  color: var(--ink-muted);
  margin-right: 4px;
  font-weight: 500;
}

/* ───── Prompt sections ───── */
.prompt-section {
  margin-bottom: 32px;
  padding-top: 20px;
  border-top: 1px solid var(--line);
}

.prompt-section:first-of-type {
  padding-top: 0;
  border-top: none;
}

.prompt-section:last-child {
  margin-bottom: 0;
}

.prompt-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 8px;
}

.prompt-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--ink);
  margin: 0 0 4px 0;
}

.prompt-desc {
  font-size: 12px;
  color: var(--ink-muted);
  margin: 0;
  line-height: 1.5;
}

.prompt-vars {
  font-size: 12px;
  color: var(--ink-muted);
  margin: 0 0 8px 0;
  line-height: 1.6;
}

.prompt-vars code {
  background: var(--bg-soft);
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 11px;
  font-family: var(--font-mono);
  color: var(--ink-soft);
  margin-right: 2px;
}

.reset-btn {
  flex-shrink: 0;
}

.prompt-textarea :deep(.el-textarea__inner) {
  font-family: var(--font-mono) !important;
  font-size: 12.5px !important;
  line-height: 1.65 !important;
  color: var(--ink) !important;
}

/* ───── Alert banner (custom, no EP blue/yellow) ───── */
.alert-banner {
  background: var(--bg-soft);
  border: 1px solid var(--line);
  border-left: 3px solid var(--ink-muted);
  border-radius: var(--radius-sm);
  padding: 12px 16px;
  margin-bottom: 16px;
}

.alert-banner.warning {
  border-left-color: var(--warning);
}

.alert-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
  margin-bottom: 4px;
}

.alert-body {
  font-size: 12.5px;
  color: var(--ink-soft);
  line-height: 1.5;
}

.alert-body a {
  color: var(--ink);
  text-decoration: underline;
  font-weight: 500;
}

/* ───── Status pill (inline) ───── */
.status-pill {
  display: inline-flex;
  align-items: center;
  font-size: 11px;
  font-weight: 500;
  padding: 2px 10px;
  border-radius: var(--radius-pill);
  background: var(--bg-soft);
  color: var(--ink-muted);
  border: 1px solid var(--line);
  white-space: nowrap;
}

.status-pill.success {
  background: rgba(16, 163, 127, 0.08);
  color: var(--success);
  border-color: rgba(16, 163, 127, 0.25);
}

.status-pill.danger {
  background: rgba(209, 69, 59, 0.08);
  color: var(--danger);
  border-color: rgba(209, 69, 59, 0.25);
}

.status-pill.muted {
  background: var(--bg-soft);
  color: var(--ink-muted);
  border-color: var(--line);
}

/* ───── Collectors table ───── */
.collector-name {
  display: flex;
  align-items: center;
  gap: 8px;
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

.status-dot.online { background: var(--success); }
.status-dot.offline { background: var(--ink-dim); }
.status-dot.paused { background: var(--warning); }

.platform-icon {
  font-size: 14px;
}

.hostname {
  color: var(--ink-muted);
  font-size: 12px;
  font-family: var(--font-mono);
}

.cell-muted {
  color: var(--ink-muted);
  font-size: 12.5px;
}

.cell-mono {
  color: var(--ink-muted);
  font-size: 12px;
  font-family: var(--font-mono);
}

.cap-pill {
  margin-right: 4px;
  margin-bottom: 2px;
}

/* ───── Responsive ───── */
@media (max-width: 700px) {
  .settings-form.two-col {
    grid-template-columns: 1fr;
  }

  .add-repo-form {
    flex-direction: column;
  }

  .page-header {
    flex-direction: column;
  }
}

/* ───── Updates tab ───── */
.update-version-row {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 24px;
  align-items: end;
}
.update-summary {
  padding: 16px;
  background: #fafafa;
  border-radius: 8px;
}
.update-progress {
  margin-top: 20px;
  padding: 16px;
  background: #f5f9ff;
  border-radius: 8px;
}
.release-notes {
  margin: 8px 0 0;
  padding: 12px;
  background: #fff;
  border: 1px solid #eee;
  border-radius: 4px;
  font-size: 12px;
  max-height: 200px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
