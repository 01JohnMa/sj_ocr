import { api } from './api'
import type {
  SDKConfirmTemplatePayload,
  SDKDocumentAnalysis,
  SDKSession,
  SDKCommitResult,
} from '@/types'

export async function createSDKSession(
  file: File,
  excelTemplate?: File | null,
): Promise<SDKSession> {
  const formData = new FormData()
  formData.append('file', file)
  if (excelTemplate) {
    formData.append('excel_template', excelTemplate)
  }
  const { data } = await api.post<SDKSession>('/sdk/sessions', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
  return data
}

export async function analyzeSDKSession(sessionId: string): Promise<SDKDocumentAnalysis> {
  const { data } = await api.post<{ success: boolean; analysis: SDKDocumentAnalysis }>(
    `/sdk/sessions/${sessionId}/analyze`,
  )
  return data.analysis
}

export async function confirmSDKTemplate(
  sessionId: string,
  payload: SDKConfirmTemplatePayload,
): Promise<SDKSession> {
  const { data } = await api.post<SDKSession>(
    `/sdk/sessions/${sessionId}/confirm-template`,
    payload,
  )
  return data
}

export async function generateSDKPrompt(sessionId: string): Promise<string> {
  const { data } = await api.post<{ success: boolean; prompt: string }>(
    `/sdk/sessions/${sessionId}/prompt`,
  )
  return data.prompt
}

export async function generateSDKCode(sessionId: string): Promise<string> {
  const { data } = await api.post<{ success: boolean; code: string }>(
    `/sdk/sessions/${sessionId}/code`,
  )
  return data.code
}

export interface SDKCommitPayload {
  prompt?: string | null
  cleaner_code?: string | null
}

export async function commitSDKSession(
  sessionId: string,
  payload: SDKCommitPayload = {},
): Promise<SDKCommitResult> {
  const { data } = await api.post<{ success: boolean; commit_result: SDKCommitResult }>(
    `/sdk/sessions/${sessionId}/commit`,
    payload,
  )
  return data.commit_result
}
