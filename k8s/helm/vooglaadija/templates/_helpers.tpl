{{/*
Expand the name of the chart.
*/}}
{{- define "vooglaadija.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "vooglaadija.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "vooglaadija.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "vooglaadija.labels" -}}
helm.sh/chart: {{ include "vooglaadija.chart" . }}
{{ include "vooglaadija.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "vooglaadija.selectorLabels" -}}
app.kubernetes.io/name: {{ include "vooglaadija.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
API selector labels
*/}}
{{- define "vooglaadija.apiSelectorLabels" -}}
app.kubernetes.io/name: {{ include "vooglaadija.name" . }}-api
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: api
{{- end }}

{{/*
Worker selector labels
*/}}
{{- define "vooglaadija.workerSelectorLabels" -}}
app.kubernetes.io/name: {{ include "vooglaadija.name" . }}-worker
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: worker
{{- end }}

{{/*
Browser-downloader selector labels
*/}}
{{- define "vooglaadija.browserDownloaderSelectorLabels" -}}
app.kubernetes.io/name: {{ include "vooglaadija.name" . }}-browser-downloader
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: browser-downloader
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "vooglaadija.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "vooglaadija.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Common environment variables
*/}}
{{- define "vooglaadija.commonEnv" -}}
- name: DB_HOST
  value: {{ include "vooglaadija.dbHost" . | quote }}
- name: DB_PORT
  value: {{ include "vooglaadija.dbPort" . | quote }}
- name: DB_NAME
  value: {{ .Values.db.name | quote }}
- name: DB_USER
  value: {{ .Values.db.user | quote }}
- name: DB_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "vooglaadija.secretName" . }}
      key: db-password
- name: REDIS_HOST
  value: {{ include "vooglaadija.redisHost" . | quote }}
- name: REDIS_PORT
  value: {{ include "vooglaadija.redisPort" . | quote }}
- name: REDIS_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "vooglaadija.secretName" . }}
      key: redis-password
- name: SECRET_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "vooglaadija.secretName" . }}
      key: secret-key
- name: CORS_ORIGINS
  value: {{ .Values.api.corsOrigins | quote }}
- name: COOKIE_SECURE
  value: {{ .Values.api.cookieSecure | quote }}
- name: ACCESS_TOKEN_EXPIRE_MINUTES
  value: {{ .Values.api.accessTokenExpireMinutes | quote }}
- name: REFRESH_TOKEN_EXPIRE_DAYS
  value: {{ .Values.api.refreshTokenExpireDays | quote }}
- name: OTEL_EXPORTER_OTLP_ENDPOINT
  value: {{ .Values.otel.endpoint | quote }}
- name: FEATURE_METRICS_ENABLED
  value: {{ .Values.featureFlags.metricsEnabled | quote }}
- name: FEATURE_TRACING_ENABLED
  value: {{ .Values.featureFlags.tracingEnabled | quote }}
- name: BROWSER_DOWNLOADER_ENABLED
  value: {{ .Values.browserDownloader.enabled | quote }}
- name: BROWSER_DOWNLOADER_ENDPOINT
  value: {{ printf "http://%s-browser-downloader:%d" (include "vooglaadija.fullname" .) (int .Values.browserDownloader.service.port) | quote }}
{{- end }}

{{/*
Database host
*/}}
{{- define "vooglaadija.dbHost" -}}
{{- if .Values.postgresql.enabled }}
{{- printf "%s-postgresql" (include "vooglaadija.fullname" .) }}
{{- else }}
{{- .Values.db.host }}
{{- end }}
{{- end }}

{{/*
Database port
*/}}
{{- define "vooglaadija.dbPort" -}}
{{- if .Values.postgresql.enabled }}
{{- "5432" }}
{{- else }}
{{- .Values.db.port | quote }}
{{- end }}
{{- end }}

{{/*
Redis host
*/}}
{{- define "vooglaadija.redisHost" -}}
{{- if .Values.redis.enabled }}
{{- printf "%s-redis-master" (include "vooglaadija.fullname" .) }}
{{- else }}
{{- .Values.redis.host }}
{{- end }}
{{- end }}

{{/*
Redis port
*/}}
{{- define "vooglaadija.redisPort" -}}
{{- if .Values.redis.enabled }}
{{- "6379" }}
{{- else }}
{{- .Values.redis.port | quote }}
{{- end }}
{{- end }}

{{/*
Secret name
*/}}
{{- define "vooglaadija.secretName" -}}
{{- if .Values.existingSecret }}
{{- .Values.existingSecret }}
{{- else }}
{{- printf "%s-secrets" (include "vooglaadija.fullname" .) }}
{{- end }}
{{- end }}
