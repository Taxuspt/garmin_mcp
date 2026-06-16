{{/* Chart name, optionally overridden. */}}
{{- define "garmin-mcp.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* Fully qualified app name. */}}
{{- define "garmin-mcp.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "garmin-mcp.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "garmin-mcp.labels" -}}
helm.sh/chart: {{ include "garmin-mcp.chart" . }}
{{ include "garmin-mcp.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "garmin-mcp.selectorLabels" -}}
app.kubernetes.io/name: {{ include "garmin-mcp.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/* Name of the Secret holding Garmin credentials (existing or chart-created). */}}
{{- define "garmin-mcp.secretName" -}}
{{- if .Values.garmin.existingSecret -}}
{{- .Values.garmin.existingSecret -}}
{{- else -}}
{{- include "garmin-mcp.fullname" . -}}
{{- end -}}
{{- end -}}

{{/* "true" when pre-generated tokens are supplied via values (option A). */}}
{{- define "garmin-mcp.tokenMode" -}}
{{- if or .Values.garmin.tokens.json .Values.garmin.tokens.base64 -}}true{{- end -}}
{{- end -}}

{{/* The token JSON to store, from raw json or decoded base64. */}}
{{- define "garmin-mcp.tokenJSON" -}}
{{- if .Values.garmin.tokens.json -}}
{{- .Values.garmin.tokens.json -}}
{{- else if .Values.garmin.tokens.base64 -}}
{{- .Values.garmin.tokens.base64 | trim | b64dec -}}
{{- end -}}
{{- end -}}

{{/* Name of the PVC for token storage (existing or chart-created). */}}
{{- define "garmin-mcp.pvcName" -}}
{{- if .Values.persistence.existingClaim -}}
{{- .Values.persistence.existingClaim -}}
{{- else -}}
{{- printf "%s-tokens" (include "garmin-mcp.fullname" .) -}}
{{- end -}}
{{- end -}}
