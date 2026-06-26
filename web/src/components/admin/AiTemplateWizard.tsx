import { useState } from 'react'
import {
  Check,
  ChevronDown,
  ChevronUp,
  Code2,
  FileText,
  Plus,
  Sparkles,
  Trash2,
  Upload,
  Wand2,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import * as sdkApi from '@/services/sdk'
import type {
  SDKCommitResult,
  SDKConfirmTemplatePayload,
  SDKDetectedField,
  SDKDocumentAnalysis,
  SDKSession,
  SDKSuggestedExample,
} from '@/types'
import { cn } from '@/lib/utils'

interface AiTemplateWizardProps {
  tenantId: string
  onCommitted: () => void | Promise<void>
}

const steps = [
  { key: 'upload', label: '上传 OCR' },
  { key: 'analyze', label: 'AI 分析' },
  { key: 'confirm', label: '确认配置' },
  { key: 'commit', label: '写入模板' },
]

const formatJson = (value: Record<string, unknown>) => JSON.stringify(value ?? {}, null, 2)

const parseAllowedValues = (value: string): string[] | null => {
  const values = value
    .split(/[,，\n]/)
    .map((item) => item.trim())
    .filter(Boolean)
  return values.length > 0 ? values : null
}

const createEmptyField = (index: number): SDKDetectedField => ({
  field_key: `field_${index + 1}`,
  field_label: '新字段',
  field_type: 'text',
  extraction_hint: '',
  review_enforced: false,
  review_allowed_values: null,
  sample_value: null,
})

const createEmptyExample = (): SDKSuggestedExample => ({
  example_input: '',
  example_output: {},
})

export function AiTemplateWizard({ tenantId, onCommitted }: AiTemplateWizardProps) {
  const [file, setFile] = useState<File | null>(null)
  const [excelTemplateFile, setExcelTemplateFile] = useState<File | null>(null)
  const [session, setSession] = useState<SDKSession | null>(null)
  const [analysis, setAnalysis] = useState<SDKDocumentAnalysis | null>(null)
  const [fields, setFields] = useState<SDKDetectedField[]>([])
  const [examples, setExamples] = useState<SDKSuggestedExample[]>([])
  const [exampleOutputDrafts, setExampleOutputDrafts] = useState<string[]>([])
  const [invalidExampleIndexes, setInvalidExampleIndexes] = useState<number[]>([])
  const [templateName, setTemplateName] = useState('')
  const [templateCode, setTemplateCode] = useState('')
  const [description, setDescription] = useState('')
  const [extractionMode, setExtractionMode] = useState<'ocr_llm' | 'vlm'>('ocr_llm')
  const [perPageExtraction, setPerPageExtraction] = useState(false)
  const [prompt, setPrompt] = useState('')
  const [cleanerCode, setCleanerCode] = useState('')
  const [configRevision, setConfigRevision] = useState(0)
  const [promptRevision, setPromptRevision] = useState<number | null>(null)
  const [codeRevision, setCodeRevision] = useState<number | null>(null)
  const [commitResult, setCommitResult] = useState<SDKCommitResult | null>(null)
  const [loadingAction, setLoadingAction] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const activeStep = commitResult ? 'commit' : analysis ? 'confirm' : session ? 'analyze' : 'upload'
  const fieldKeys = fields.map((field) => field.field_key.trim()).filter(Boolean)
  const hasFieldErrors = fields.some(
    (field) => !field.field_key.trim() || !field.field_label.trim(),
  )
  const hasDuplicateFieldKeys = fieldKeys.length !== new Set(fieldKeys).size
  const hasInvalidExamples = invalidExampleIndexes.length > 0
  const promptIsCurrent = promptRevision === configRevision
  const codeIsCurrent = codeRevision === configRevision
  const canCommit = Boolean(
    session
      && templateName.trim()
      && templateCode.trim()
      && fields.length > 0
      && !hasFieldErrors
      && !hasDuplicateFieldKeys
      && !hasInvalidExamples,
  )

  const validationMessage = (() => {
    if (!analysis) return null
    if (!templateName.trim() || !templateCode.trim()) return '模板名称和 code 不能为空'
    if (fields.length === 0) return '至少需要 1 个识别字段'
    if (hasFieldErrors) return '字段键名和标签不能为空'
    if (hasDuplicateFieldKeys) return '字段键名不能重复'
    if (hasInvalidExamples) return '示例输出必须是合法 JSON'
    return null
  })()

  const markConfigChanged = () => {
    setConfigRevision((revision) => revision + 1)
  }

  const resetGeneratedArtifacts = () => {
    setPrompt('')
    setCleanerCode('')
    setPromptRevision(null)
    setCodeRevision(null)
  }

  const resetAnalysisState = () => {
    setAnalysis(null)
    setFields([])
    setExamples([])
    setExampleOutputDrafts([])
    setInvalidExampleIndexes([])
    setTemplateName('')
    setTemplateCode('')
    setDescription('')
    setExtractionMode('ocr_llm')
    setPerPageExtraction(false)
    setConfigRevision(0)
    resetGeneratedArtifacts()
    setCommitResult(null)
  }

  const runAction = async (name: string, action: () => Promise<void>) => {
    setLoadingAction(name)
    setError(null)
    try {
      await action()
    } catch (err) {
      setError(err instanceof Error ? err.message : '操作失败')
    } finally {
      setLoadingAction(null)
    }
  }

  const handleCreateSession = () => {
    if (!file) return
    void runAction('upload', async () => {
      const created = await sdkApi.createSDKSession(file, excelTemplateFile)
      setSession(created)
      resetAnalysisState()
    })
  }

  const handleAnalyze = () => {
    if (!session) return
    void runAction('analyze', async () => {
      const result = await sdkApi.analyzeSDKSession(session.id)
      setAnalysis(result)
      setFields(result.detected_fields)
      setExamples(result.suggested_examples)
      setExampleOutputDrafts(result.suggested_examples.map((example) => formatJson(example.example_output)))
      setInvalidExampleIndexes([])
      setTemplateName(result.recommended_doc_type)
      setTemplateCode(result.recommended_doc_code)
      setDescription(`由 AI 根据 ${session.file_name} 生成`)
      setExtractionMode('ocr_llm')
      setPerPageExtraction(false)
      setConfigRevision((revision) => revision + 1)
      resetGeneratedArtifacts()
      setCommitResult(null)
    })
  }

  const buildConfirmPayload = (): SDKConfirmTemplatePayload => ({
    template_name: templateName.trim(),
    template_code: templateCode.trim(),
    description: description.trim() || null,
    tenant_id: tenantId,
    tenant_name: null,
    tenant_code: null,
    extraction_mode: extractionMode,
    per_page_extraction: perPageExtraction,
    fields: fields.map((field) => ({
      ...field,
      field_key: field.field_key.trim(),
      field_label: field.field_label.trim(),
      extraction_hint: field.extraction_hint.trim(),
      review_allowed_values: field.review_allowed_values?.length ? field.review_allowed_values : null,
    })),
    examples: examples.map((example) => ({
      example_input: example.example_input.trim(),
      example_output: example.example_output,
    })),
  })

  const confirmCurrentTemplate = async () => {
    if (!session) return
    await sdkApi.confirmSDKTemplate(session.id, buildConfirmPayload())
  }

  const handleGeneratePrompt = () => {
    if (!session || !canCommit) return
    void runAction('prompt', async () => {
      await confirmCurrentTemplate()
      const generated = await sdkApi.generateSDKPrompt(session.id)
      setPrompt(generated)
      setPromptRevision(configRevision)
    })
  }

  const handleGenerateCode = () => {
    if (!session || !canCommit) return
    void runAction('code', async () => {
      await confirmCurrentTemplate()
      const generated = await sdkApi.generateSDKCode(session.id)
      setCleanerCode(generated)
      setCodeRevision(configRevision)
    })
  }

  const handleCommit = () => {
    if (!session || !canCommit) return
    void runAction('commit', async () => {
      await confirmCurrentTemplate()
      let finalPrompt = prompt
      if (!finalPrompt.trim() || !promptIsCurrent) {
        finalPrompt = await sdkApi.generateSDKPrompt(session.id)
        setPrompt(finalPrompt)
        setPromptRevision(configRevision)
      }
      const result = await sdkApi.commitSDKSession(session.id, {
        prompt: finalPrompt,
        cleaner_code: codeIsCurrent ? cleanerCode || null : null,
      })
      setCommitResult(result)
      await onCommitted()
    })
  }

  const updateTemplateField = (patch: {
    templateName?: string
    templateCode?: string
    description?: string
    extractionMode?: 'ocr_llm' | 'vlm'
    perPageExtraction?: boolean
  }) => {
    if (patch.templateName !== undefined) setTemplateName(patch.templateName)
    if (patch.templateCode !== undefined) setTemplateCode(patch.templateCode)
    if (patch.description !== undefined) setDescription(patch.description)
    if (patch.extractionMode !== undefined) setExtractionMode(patch.extractionMode)
    if (patch.perPageExtraction !== undefined) setPerPageExtraction(patch.perPageExtraction)
    markConfigChanged()
  }

  const updateField = (index: number, patch: Partial<SDKDetectedField>) => {
    setFields((prev) => prev.map((field, i) => (i === index ? { ...field, ...patch } : field)))
    markConfigChanged()
  }

  const moveField = (index: number, direction: 'up' | 'down') => {
    setFields((prev) => {
      const swapIndex = direction === 'up' ? index - 1 : index + 1
      if (swapIndex < 0 || swapIndex >= prev.length) return prev
      const next = [...prev]
      ;[next[index], next[swapIndex]] = [next[swapIndex], next[index]]
      return next
    })
    markConfigChanged()
  }

  const addField = () => {
    setFields((prev) => [...prev, createEmptyField(prev.length)])
    markConfigChanged()
  }

  const removeField = (index: number) => {
    setFields((prev) => prev.filter((_, i) => i !== index))
    markConfigChanged()
  }

  const updateExampleInput = (index: number, value: string) => {
    setExamples((prev) => (
      prev.map((example, i) => (i === index ? { ...example, example_input: value } : example))
    ))
    markConfigChanged()
  }

  const updateExampleOutput = (index: number, value: string) => {
    setExampleOutputDrafts((prev) => prev.map((draft, i) => (i === index ? value : draft)))
    try {
      const parsed = value.trim() ? JSON.parse(value) : {}
      setExamples((prev) => (
        prev.map((example, i) => (i === index ? { ...example, example_output: parsed } : example))
      ))
      setInvalidExampleIndexes((prev) => prev.filter((i) => i !== index))
      markConfigChanged()
    } catch {
      setInvalidExampleIndexes((prev) => (prev.includes(index) ? prev : [...prev, index]))
    }
  }

  const moveExample = (index: number, direction: 'up' | 'down') => {
    const swapIndex = direction === 'up' ? index - 1 : index + 1
    if (swapIndex < 0 || swapIndex >= examples.length) return
    setExamples((prev) => {
      const next = [...prev]
      ;[next[index], next[swapIndex]] = [next[swapIndex], next[index]]
      return next
    })
    setExampleOutputDrafts((prev) => {
      const next = [...prev]
      ;[next[index], next[swapIndex]] = [next[swapIndex], next[index]]
      return next
    })
    setInvalidExampleIndexes((prev) => prev.map((i) => {
      if (i === index) return swapIndex
      if (i === swapIndex) return index
      return i
    }))
    markConfigChanged()
  }

  const addExample = () => {
    setExamples((prev) => [...prev, createEmptyExample()])
    setExampleOutputDrafts((prev) => [...prev, '{}'])
    markConfigChanged()
  }

  const removeExample = (index: number) => {
    setExamples((prev) => prev.filter((_, i) => i !== index))
    setExampleOutputDrafts((prev) => prev.filter((_, i) => i !== index))
    setInvalidExampleIndexes((prev) => (
      prev
        .filter((i) => i !== index)
        .map((i) => (i > index ? i - 1 : i))
    ))
    markConfigChanged()
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap gap-2">
        {steps.map((step) => (
          <div
            key={step.key}
            className={cn(
              'flex items-center gap-2 rounded-lg border px-3 py-2 text-xs font-medium',
              activeStep === step.key
                ? 'border-primary-500/40 bg-primary-500/10 text-primary-300'
                : 'border-border-default text-text-muted',
            )}
          >
            {commitResult && step.key === 'commit' ? <Check className="h-3.5 w-3.5" /> : null}
            {step.label}
          </div>
        ))}
      </div>

      {error && (
        <div className="rounded-lg border border-error-500/30 bg-error-500/10 px-4 py-3 text-sm text-error-500">
          {error}
        </div>
      )}

      {validationMessage && (
        <div className="rounded-lg border border-warning-500/30 bg-warning-500/10 px-4 py-3 text-sm text-warning-400">
          {validationMessage}
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,0.85fr)_minmax(0,1.15fr)]">
        <div className="space-y-4">
          <div className="rounded-lg border border-border-default bg-bg-secondary p-4">
            <div className="mb-3 flex items-center gap-2 text-sm font-medium text-text-primary">
              <Upload className="h-4 w-4 text-primary-400" />
              待识别图片/文档
            </div>
            <p className="mb-3 text-xs leading-relaxed text-text-muted">
              这里上传需要解析的图片或文档。固定 Excel 模式下，字段值仍然来自这个文件。
            </p>
            <Input
              type="file"
              accept=".pdf,.png,.jpg,.jpeg,.tiff,.bmp"
              onChange={(event) => {
                setFile(event.target.files?.[0] ?? null)
                setSession(null)
                resetAnalysisState()
              }}
            />
            <div className="mt-4 rounded-lg border border-border-default bg-bg-card p-3">
              <Label>空 Excel 填报模板（可选）</Label>
              <p className="mb-2 mt-1 text-xs leading-relaxed text-text-muted">
                模板只放 <span className="font-mono">{'{{field_key}}'}</span> 槽位；系统会扫描槽位，再把上方文件的解析结果填进去。
              </p>
              <Input
                type="file"
                accept=".xlsx,.xlsm"
                onChange={(event) => {
                  setExcelTemplateFile(event.target.files?.[0] ?? null)
                  setSession(null)
                  resetAnalysisState()
                }}
              />
              {excelTemplateFile && (
                <p className="mt-2 text-xs text-text-muted">
                  已选择：{excelTemplateFile.name}
                </p>
              )}
            </div>
            <Button
              className="mt-3"
              size="sm"
              onClick={handleCreateSession}
              disabled={!file}
              loading={loadingAction === 'upload'}
            >
              上传并解析
            </Button>
          </div>

          {session && (
            <div className="rounded-lg border border-border-default bg-bg-secondary p-4">
              <div className="mb-3 flex items-center gap-2 text-sm font-medium text-text-primary">
                <FileText className="h-4 w-4 text-primary-400" />
                OCR 摘要
              </div>
              <p className="text-xs text-text-muted">{session.file_name}</p>
              <p className="mt-1 text-xs text-text-muted">
                置信度 {(session.ocr_confidence * 100).toFixed(1)}%
              </p>
              {session.excel_template_file_name && (
                <div className="mt-3 rounded-lg border border-border-default bg-bg-card p-3">
                  <p className="text-xs font-medium text-text-primary">
                    Excel 空模板：{session.excel_template_file_name}
                  </p>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {session.excel_placeholders.map((placeholder) => (
                      <span
                        key={`${placeholder.sheet_name}-${placeholder.coordinate}-${placeholder.field_key}`}
                        className="rounded-full border border-primary-500/30 bg-primary-500/10 px-2 py-1 font-mono text-xs text-primary-300"
                      >
                        {placeholder.field_key}
                      </span>
                    ))}
                  </div>
                  {session.excel_placeholders.length === 0 && (
                    <p className="mt-2 text-xs text-warning-400">
                      未扫描到 {'{{field_key}}'} 槽位，请检查空模板。
                    </p>
                  )}
                </div>
              )}
              <Textarea className="mt-3 min-h-[160px]" value={session.ocr_text} readOnly />
              <Button
                className="mt-3"
                size="sm"
                variant="secondary"
                onClick={handleAnalyze}
                loading={loadingAction === 'analyze'}
              >
                <Sparkles className="h-4 w-4" />
                分析文档
              </Button>
            </div>
          )}

          {analysis && (
            <div className="rounded-lg border border-border-default bg-bg-secondary p-4">
              <div className="mb-2 text-sm font-medium text-text-primary">AI 推荐</div>
              <div className="space-y-2 text-xs text-text-secondary">
                <p>置信度 {(analysis.confidence * 100).toFixed(1)}%</p>
                <p>推荐部门：{analysis.recommended_tenant.suggest_name}</p>
                {analysis.recommended_tenant.reason && (
                  <p className="text-text-muted">{analysis.recommended_tenant.reason}</p>
                )}
              </div>
            </div>
          )}
        </div>

        <div className="space-y-4">
          {analysis && (
            <>
              <div className="rounded-lg border border-border-default bg-bg-secondary p-4">
                <div className="mb-3 flex items-center gap-2 text-sm font-medium text-text-primary">
                  <Wand2 className="h-4 w-4 text-primary-400" />
                  模板信息
                </div>
                <div className="grid gap-3 md:grid-cols-2">
                  <div>
                    <Label>模板名称</Label>
                    <Input
                      className="mt-1"
                      value={templateName}
                      onChange={(e) => updateTemplateField({ templateName: e.target.value })}
                    />
                  </div>
                  <div>
                    <Label>模板 code</Label>
                    <Input
                      className="mt-1"
                      value={templateCode}
                      onChange={(e) => updateTemplateField({ templateCode: e.target.value })}
                    />
                  </div>
                  <div>
                    <Label>提取模式</Label>
                    <Select
                      className="mt-1"
                      value={extractionMode}
                      onChange={(e) => (
                        updateTemplateField({ extractionMode: e.target.value as 'ocr_llm' | 'vlm' })
                      )}
                    >
                      <option value="ocr_llm">OCR + LLM</option>
                      <option value="vlm">VLM</option>
                    </Select>
                  </div>
                  <label className="flex items-end gap-2 pb-2 text-sm text-text-secondary">
                    <input
                      type="checkbox"
                      checked={perPageExtraction}
                      onChange={(e) => updateTemplateField({ perPageExtraction: e.target.checked })}
                    />
                    逐页提取
                  </label>
                </div>
                <Label className="mt-3 block">描述</Label>
                <Textarea
                  className="mt-1"
                  value={description}
                  onChange={(e) => updateTemplateField({ description: e.target.value })}
                />
              </div>

              <div className="rounded-lg border border-border-default bg-bg-secondary p-4">
                <div className="mb-3 flex items-center justify-between gap-3">
                  <div className="text-sm font-medium text-text-primary">识别字段</div>
                  <Button size="sm" variant="secondary" onClick={addField}>
                    <Plus className="h-4 w-4" />
                    新增字段
                  </Button>
                </div>
                <div className="space-y-3">
                  {fields.map((field, index) => (
                    <div
                      key={`${field.field_key}-${index}`}
                      className="rounded-lg border border-border-default p-3"
                    >
                      <div className="mb-2 flex items-center justify-between gap-2">
                        <span className="text-xs font-medium text-text-muted">字段 {index + 1}</span>
                        <div className="flex items-center gap-1">
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            onClick={() => moveField(index, 'up')}
                            disabled={index === 0}
                          >
                            <ChevronUp className="h-3.5 w-3.5" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            onClick={() => moveField(index, 'down')}
                            disabled={index === fields.length - 1}
                          >
                            <ChevronDown className="h-3.5 w-3.5" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            className="hover:text-error-500"
                            onClick={() => removeField(index)}
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </Button>
                        </div>
                      </div>
                      <div className="grid gap-2 md:grid-cols-[1fr_1fr_120px]">
                        <div>
                          <Label>字段标签</Label>
                          <Input
                            className="mt-1"
                            value={field.field_label}
                            onChange={(e) => updateField(index, { field_label: e.target.value })}
                          />
                        </div>
                        <div>
                          <Label>字段键名</Label>
                          <Input
                            className="mt-1 font-mono"
                            value={field.field_key}
                            onChange={(e) => updateField(index, { field_key: e.target.value })}
                          />
                        </div>
                        <div>
                          <Label>类型</Label>
                          <Select
                            className="mt-1"
                            value={field.field_type}
                            onChange={(e) => (
                              updateField(index, {
                                field_type: e.target.value as SDKDetectedField['field_type'],
                              })
                            )}
                          >
                            <option value="text">text</option>
                            <option value="date">date</option>
                            <option value="number">number</option>
                          </Select>
                        </div>
                        <div className="md:col-span-3">
                          <Label>提取提示</Label>
                          <Textarea
                            className="mt-1"
                            value={field.extraction_hint}
                            onChange={(e) => updateField(index, { extraction_hint: e.target.value })}
                          />
                        </div>
                        <label className="flex items-center gap-2 text-sm text-text-secondary">
                          <input
                            type="checkbox"
                            checked={field.review_enforced}
                            onChange={(e) => updateField(index, { review_enforced: e.target.checked })}
                          />
                          审核必填
                        </label>
                        <div className="md:col-span-2">
                          <Label>允许值</Label>
                          <Input
                            className="mt-1"
                            value={(field.review_allowed_values ?? []).join(', ')}
                            onChange={(e) => (
                              updateField(index, { review_allowed_values: parseAllowedValues(e.target.value) })
                            )}
                          />
                        </div>
                      </div>
                      {field.sample_value && (
                        <p className="mt-2 text-xs text-text-muted">样例值：{field.sample_value}</p>
                      )}
                    </div>
                  ))}
                </div>
              </div>

              <div className="rounded-lg border border-border-default bg-bg-secondary p-4">
                <div className="mb-3 flex items-center justify-between gap-3">
                  <div className="text-sm font-medium text-text-primary">Few-shot 示例</div>
                  <Button size="sm" variant="secondary" onClick={addExample}>
                    <Plus className="h-4 w-4" />
                    新增示例
                  </Button>
                </div>
                <div className="space-y-3">
                  {examples.map((example, index) => (
                    <div key={index} className="rounded-lg border border-border-default p-3">
                      <div className="mb-2 flex items-center justify-between gap-2">
                        <span className="text-xs font-medium text-text-muted">示例 {index + 1}</span>
                        <div className="flex items-center gap-1">
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            onClick={() => moveExample(index, 'up')}
                            disabled={index === 0}
                          >
                            <ChevronUp className="h-3.5 w-3.5" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            onClick={() => moveExample(index, 'down')}
                            disabled={index === examples.length - 1}
                          >
                            <ChevronDown className="h-3.5 w-3.5" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            className="hover:text-error-500"
                            onClick={() => removeExample(index)}
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </Button>
                        </div>
                      </div>
                      <div className="grid gap-2 md:grid-cols-2">
                        <div>
                          <Label>输入文本</Label>
                          <Textarea
                            className="mt-1 min-h-[130px] font-mono text-xs"
                            value={example.example_input}
                            onChange={(e) => updateExampleInput(index, e.target.value)}
                          />
                        </div>
                        <div>
                          <Label>期望输出 JSON</Label>
                          <Textarea
                            className="mt-1 min-h-[130px] font-mono text-xs"
                            error={invalidExampleIndexes.includes(index)}
                            value={exampleOutputDrafts[index] ?? '{}'}
                            onChange={(e) => updateExampleOutput(index, e.target.value)}
                          />
                          {invalidExampleIndexes.includes(index) && (
                            <p className="mt-1 text-xs text-error-500">JSON 格式有误</p>
                          )}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              <div className="flex flex-wrap gap-2">
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={handleGeneratePrompt}
                  disabled={!canCommit}
                  loading={loadingAction === 'prompt'}
                >
                  <Sparkles className="h-4 w-4" />
                  生成 Prompt
                </Button>
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={handleGenerateCode}
                  disabled={!canCommit}
                  loading={loadingAction === 'code'}
                >
                  <Code2 className="h-4 w-4" />
                  生成清洗代码
                </Button>
                <Button
                  size="sm"
                  onClick={handleCommit}
                  disabled={!canCommit}
                  loading={loadingAction === 'commit'}
                >
                  写入 Supabase
                </Button>
              </div>

              {prompt && (
                <div className="rounded-lg border border-border-default bg-bg-secondary p-4">
                  <div className="mb-3 flex items-center justify-between gap-3">
                    <div className="text-sm font-medium text-text-primary">Prompt 预览</div>
                    {!promptIsCurrent && (
                      <span className="text-xs text-warning-400">配置已更新</span>
                    )}
                  </div>
                  <Textarea
                    className="min-h-[220px] font-mono text-xs"
                    value={prompt}
                    onChange={(e) => {
                      setPrompt(e.target.value)
                      setPromptRevision(configRevision)
                    }}
                  />
                </div>
              )}

              {cleanerCode && (
                <div className="rounded-lg border border-border-default bg-bg-secondary p-4">
                  <div className="mb-3 flex items-center justify-between gap-3">
                    <div className="text-sm font-medium text-text-primary">清洗代码预览</div>
                    {!codeIsCurrent && (
                      <span className="text-xs text-warning-400">配置已更新</span>
                    )}
                  </div>
                  <Textarea
                    className="min-h-[220px] font-mono text-xs"
                    value={cleanerCode}
                    onChange={(e) => {
                      setCleanerCode(e.target.value)
                      setCodeRevision(configRevision)
                    }}
                  />
                </div>
              )}
            </>
          )}

          {commitResult && (
            <div className="rounded-lg border border-success-500/30 bg-success-500/10 px-4 py-3 text-sm text-success-500">
              已写入模板 {commitResult.template_id}，新增 {commitResult.field_ids.length} 个字段和 {commitResult.example_ids.length} 个示例。
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
