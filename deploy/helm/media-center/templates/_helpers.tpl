{{- define "media-center.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "media-center.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name (include "media-center.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "media-center.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "media-center.labels" -}}
helm.sh/chart: {{ include "media-center.chart" . }}
app.kubernetes.io/name: {{ include "media-center.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "media-center.serviceFullname" -}}
{{- printf "%s-%s" (include "media-center.fullname" .root) .serviceName | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "media-center.serviceAccountName" -}}
{{- include "media-center.serviceFullname" . -}}
{{- end -}}

{{- define "media-center.tokenReviewName" -}}
{{- $base := include "media-center.serviceFullname" . -}}
{{- printf "%s-tokenreview" ($base | trunc 51 | trimSuffix "-") | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "media-center.selectorLabels" -}}
app.kubernetes.io/name: {{ include "media-center.name" .root }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .serviceName }}
{{- end -}}
