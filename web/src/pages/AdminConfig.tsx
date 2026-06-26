import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useProfileStore } from '@/store/useStore'
import { api } from '@/services/api'
import * as adminApi from '@/services/admin'
import type { AdminTemplate } from '@/types'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { Card } from '@/components/ui/card'
import { Spinner } from '@/components/ui/spinner'
import { Settings, Sparkles, ToggleLeft, ToggleRight } from 'lucide-react'
import { cn } from '@/lib/utils'
import { FeishuConfigTab } from './AdminFeishuTab'
import { FieldsTab } from './AdminFieldsTab'
import { ExamplesTab } from './AdminExamplesTab'
import { AiTemplateWizard } from '@/components/admin/AiTemplateWizard'

type Tab = 'ai' | 'feishu' | 'fields' | 'examples'

interface Tenant {
  id: string
  name: string
  code: string
}

export function AdminConfig() {
  const navigate = useNavigate()
  const { profile } = useProfileStore()
  const isSuperAdmin = profile?.role === 'super_admin'
  const isTenantAdmin = profile?.role === 'tenant_admin' || isSuperAdmin

  const [tenants, setTenants] = useState<Tenant[]>([])
  const [selectedTenantId, setSelectedTenantId] = useState<string>('')
  const [pairedBatchMode, setPairedBatchMode] = useState(false)
  const [savingSettings, setSavingSettings] = useState(false)
  const [templates, setTemplates] = useState<AdminTemplate[]>([])
  const [selectedTemplateId, setSelectedTemplateId] = useState<string>('')
  const [selectedTemplate, setSelectedTemplate] = useState<AdminTemplate | null>(null)
  const [loadingTemplates, setLoadingTemplates] = useState(false)
  const [activeTab, setActiveTab] = useState<Tab>('feishu')

  useEffect(() => {
    if (profile && !isTenantAdmin) {
      navigate('/', { replace: true })
    }
  }, [profile, isTenantAdmin, navigate])

  useEffect(() => {
    if (!isSuperAdmin) return
    api.get<Tenant[]>('/tenants').then(({ data }) => setTenants(data || []))
  }, [isSuperAdmin])

  useEffect(() => {
    if (!isSuperAdmin && profile?.tenant_id) {
      setSelectedTenantId(profile.tenant_id)
    }
  }, [isSuperAdmin, profile])

  useEffect(() => {
    if (!selectedTenantId) return
    setLoadingTemplates(true)
    setSelectedTemplateId('')
    setSelectedTemplate(null)
    adminApi
      .fetchAdminTemplates(selectedTenantId)
      .then(setTemplates)
      .finally(() => setLoadingTemplates(false))

    // 加载部门配置
    api.get<Tenant>(`/tenants/${selectedTenantId}`).then(({ data }) => {
      const settings = (data as Tenant & { settings?: { paired_batch_mode?: boolean } }).settings
      setPairedBatchMode(settings?.paired_batch_mode ?? false)
    })
  }, [selectedTenantId])

  const handleTogglePairedBatchMode = async () => {
    if (!selectedTenantId || savingSettings) return
    const newValue = !pairedBatchMode
    setSavingSettings(true)
    try {
      await api.put(`/tenants/${selectedTenantId}/settings`, {
        paired_batch_mode: newValue,
      })
      setPairedBatchMode(newValue)
    } catch (error) {
      console.error('更新部门配置失败:', error)
    } finally {
      setSavingSettings(false)
    }
  }

  const handleTemplateChange = (id: string) => {
    setSelectedTemplateId(id)
    setSelectedTemplate(templates.find((t) => t.id === id) ?? null)
    setActiveTab(id ? 'feishu' : 'ai')
  }

  const tabs: { key: Tab; label: string }[] = [
    { key: 'ai', label: 'AI 生成模板' },
    { key: 'feishu', label: '飞书表格配置' },
    { key: 'fields', label: '识别字段管理' },
    { key: 'examples', label: 'Few-shot 示例' },
  ]

  const refreshTemplates = async () => {
    if (!selectedTenantId) return
    setLoadingTemplates(true)
    try {
      const fresh = await adminApi.fetchAdminTemplates(selectedTenantId)
      setTemplates(fresh)
    } finally {
      setLoadingTemplates(false)
    }
  }

  if (!profile) {
    return (
      <div className="flex items-center justify-center py-24">
        <Spinner />
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <Settings className="h-6 w-6 text-primary-400" />
        <div>
          <h1 className="text-xl font-semibold text-text-primary">系统配置</h1>
          <p className="text-sm text-text-muted">配置文档模板的识别字段、审核规则和 Few-shot 示例</p>
        </div>
      </div>

      <Card className="p-4">
        <div className="flex items-center justify-between rounded-lg border border-border-default bg-bg-secondary p-4">
          <div>
            <p className="text-sm font-medium text-text-primary">配对批处理</p>
            <p className="text-xs text-text-muted">
              开启后，批量上传每组支持上传两个文件合并处理；关闭时每组仅上传一个文件
            </p>
          </div>
          <button
            type="button"
            onClick={handleTogglePairedBatchMode}
            disabled={!selectedTenantId || savingSettings}
            className="text-primary-400 hover:text-primary-300 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {pairedBatchMode ? (
              <ToggleRight className="h-8 w-8" />
            ) : (
              <ToggleLeft className="h-8 w-8 text-text-muted" />
            )}
          </button>
        </div>
      </Card>

      <Card className="p-4">
        <div className="flex flex-wrap items-end gap-4">
          {isSuperAdmin && (
            <div className="min-w-[200px]">
              <Label>选择部门</Label>
              <Select
                className="mt-1"
                value={selectedTenantId}
                onChange={(e) => setSelectedTenantId(e.target.value)}
              >
                <option value="">— 请选择部门 —</option>
                {tenants.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
              </Select>
            </div>
          )}

          {!isSuperAdmin && profile?.tenant_name && (
            <div className="min-w-[160px]">
              <Label>所属部门</Label>
              <p className="mt-1 h-10 flex items-center px-3 rounded-lg border border-border-default bg-bg-secondary text-sm text-text-secondary">
                {profile.tenant_name}
              </p>
            </div>
          )}

          <div className="min-w-[240px]">
            <Label>选择模板</Label>
            {loadingTemplates ? (
              <div className="mt-1 h-10 flex items-center px-3">
                <Spinner size="sm" />
              </div>
            ) : (
              <Select
                className="mt-1"
                value={selectedTemplateId}
                onChange={(e) => handleTemplateChange(e.target.value)}
                disabled={!selectedTenantId}
              >
                <option value="">— 请选择模板 —</option>
                {templates.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
              </Select>
            )}
          </div>

          <button
            type="button"
            onClick={() => {
              setSelectedTemplateId('')
              setSelectedTemplate(null)
              setActiveTab('ai')
            }}
            disabled={!selectedTenantId}
            className="mb-0 h-10 inline-flex items-center gap-2 rounded-lg border border-primary-500/40 px-4 text-sm font-medium text-primary-300 hover:bg-primary-500/10 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Sparkles className="h-4 w-4" />
            AI 生成模板
          </button>
        </div>
      </Card>

      {selectedTenantId ? (
        <div className="space-y-4">
          <div className="flex gap-1 rounded-xl border border-border-default bg-bg-secondary p-1 w-fit">
            {tabs.map((tab) => (
              <button
                key={tab.key}
                onClick={() => setActiveTab(tab.key)}
                disabled={tab.key !== 'ai' && !selectedTemplate}
                className={cn(
                  'rounded-lg px-4 py-2 text-sm font-medium transition-all duration-200',
                  activeTab === tab.key
                    ? 'bg-primary-500/10 text-primary-400 border border-primary-500/20'
                    : 'text-text-secondary hover:text-text-primary',
                  tab.key !== 'ai' && !selectedTemplate && 'cursor-not-allowed opacity-50 hover:text-text-secondary',
                )}
              >
                {tab.label}
              </button>
            ))}
          </div>

          <Card className="p-6">
            {activeTab === 'ai' && (
              <AiTemplateWizard tenantId={selectedTenantId} onCommitted={refreshTemplates} />
            )}
            {activeTab === 'feishu' && selectedTemplate && (
              <FeishuConfigTab
                template={selectedTemplate}
                onSaved={(updated) => {
                  setSelectedTemplate(updated)
                  setTemplates((prev) => prev.map((t) => (t.id === updated.id ? { ...t, ...updated } : t)))
                }}
              />
            )}
            {activeTab === 'fields' && selectedTemplate && <FieldsTab templateId={selectedTemplate.id} />}
            {activeTab === 'examples' && selectedTemplate && <ExamplesTab templateId={selectedTemplate.id} />}
            {activeTab !== 'ai' && !selectedTemplate && (
              <div className="flex flex-col items-center justify-center py-16 text-text-muted">
                <Settings className="h-10 w-10 mb-4 opacity-20" />
                <p className="text-sm">请选择要配置的模板</p>
              </div>
            )}
          </Card>
        </div>
      ) : (
        <div className="flex flex-col items-center justify-center py-24 text-text-muted">
          <Settings className="h-12 w-12 mb-4 opacity-20" />
          <p className="text-sm">
            {!selectedTenantId ? '请先选择部门' : '请选择要配置的模板'}
          </p>
        </div>
      )}
    </div>
  )
}
