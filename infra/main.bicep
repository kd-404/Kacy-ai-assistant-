// Hosting for Kacy: one always-on Container App (API + web UI) with its SQLite file on an
// Azure Files share. The AI services (Foundry, Search, Content Safety, Language) already
// exist and are passed in by address; this file does not create or change them.
//
// Cost note: one replica, 0.5 vCPU / 1 GiB, always on, plus a small storage account and logs.
// Check the Azure pricing calculator for your region; it is roughly the price of a small VM.

targetScope = 'resourceGroup'

param location string = resourceGroup().location
param namePrefix string = 'kacy'

@description('Container image, e.g. ghcr.io/kd-404/kacy:latest (public, so no registry login is needed).')
param image string

param foundryProjectEndpoint string
param foundryModelDeployment string = 'gpt-4.1-mini'
param foundryAgentName string = 'kacy'
param searchEndpoint string
param searchIndexName string = 'kacy-handbook'
param contentSafetyEndpoint string
param languageEndpoint string
param userRoles string
param timezone string = 'Asia/Kolkata'

@secure()
param searchApiKey string
@secure()
param contentSafetyKey string
@secure()
param languageKey string
@secure()
param googleClientId string
@secure()
param googleClientSecret string

// The app's address is predictable from the environment, so the Google redirect URI needs no second deploy.
var appUrl = 'https://${namePrefix}-app.${env.properties.defaultDomain}'
var storageName = toLower(replace('${namePrefix}data${uniqueString(resourceGroup().id)}', '-', ''))

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${namePrefix}-logs'
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: take(storageName, 24)
  location: location
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    supportsHttpsTrafficOnly: true
  }
}

resource fileService 'Microsoft.Storage/storageAccounts/fileServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource share 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-05-01' = {
  parent: fileService
  name: 'kacy-data'
  properties: { shareQuota: 5 }
}

resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${namePrefix}-env'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

resource envStorage 'Microsoft.App/managedEnvironments/storages@2024-03-01' = {
  parent: env
  name: 'kacydata'
  properties: {
    azureFile: {
      accountName: storage.name
      accountKey: storage.listKeys().keys[0].value
      shareName: share.name
      accessMode: 'ReadWrite'
    }
  }
}

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${namePrefix}-app'
  location: location
  // Managed identity: the app signs in to Foundry as itself, so no Foundry key exists anywhere.
  identity: { type: 'SystemAssigned' }
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
      }
      secrets: [
        { name: 'search-key', value: searchApiKey }
        { name: 'content-safety-key', value: contentSafetyKey }
        { name: 'language-key', value: languageKey }
        { name: 'google-client-id', value: googleClientId }
        { name: 'google-client-secret', value: googleClientSecret }
      ]
    }
    template: {
      // Exactly one copy: sign-in sessions, rate limits and the SQLite file all assume it.
      scale: { minReplicas: 1, maxReplicas: 1 }
      containers: [
        {
          name: 'kacy'
          image: image
          resources: { cpu: json('0.5'), memory: '1Gi' }
          volumeMounts: [{ volumeName: 'data', mountPath: '/data' }]
          probes: [
            { type: 'Liveness', httpGet: { path: '/health', port: 8000 }, periodSeconds: 30 }
            { type: 'Readiness', httpGet: { path: '/health', port: 8000 }, periodSeconds: 10 }
          ]
          env: [
            { name: 'ENVIRONMENT', value: 'prod' }
            { name: 'KACY_DB', value: '/data/kacy.db' }
            { name: 'KACY_TZ', value: timezone }
            { name: 'KACY_USER_ROLES', value: userRoles }
            { name: 'FOUNDRY_PROJECT_ENDPOINT', value: foundryProjectEndpoint }
            { name: 'FOUNDRY_MODEL_DEPLOYMENT', value: foundryModelDeployment }
            { name: 'FOUNDRY_AGENT_NAME', value: foundryAgentName }
            { name: 'SEARCH_ENDPOINT', value: searchEndpoint }
            { name: 'SEARCH_INDEX_NAME', value: searchIndexName }
            { name: 'SEARCH_API_KEY', secretRef: 'search-key' }
            { name: 'CONTENT_SAFETY_ENDPOINT', value: contentSafetyEndpoint }
            { name: 'CONTENT_SAFETY_KEY', secretRef: 'content-safety-key' }
            { name: 'LANGUAGE_ENDPOINT', value: languageEndpoint }
            { name: 'LANGUAGE_KEY', secretRef: 'language-key' }
            { name: 'GOOGLE_CLIENT_ID', secretRef: 'google-client-id' }
            { name: 'GOOGLE_CLIENT_SECRET', secretRef: 'google-client-secret' }
            { name: 'GOOGLE_REDIRECT_URI', value: '${appUrl}/auth/google/callback' }
            { name: 'LOG_PII_DEMO', value: '0' }
          ]
        }
      ]
      volumes: [
        { name: 'data', storageType: 'AzureFile', storageName: envStorage.name }
      ]
    }
  }
}

output appUrl string = 'https://${app.properties.configuration.ingress.fqdn}'
output principalId string = app.identity.principalId
