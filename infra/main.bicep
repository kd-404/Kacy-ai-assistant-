// Minimal Foundry-centric infra skeleton for Kacy.
// Intentionally small: one Foundry account/project, one AI Search service (free tier),
// one Content Safety + Language resource, one Cosmos DB (always-free tier) for mocked
// tool backends. Expand per-service as each build phase lands.
//
// Before running `azd up` on an Azure for Students subscription, check the allowed
// regions in Policy > Assignments (see docs/architecture.md section 0) and pass that
// region as `location`.

targetScope = 'resourceGroup'

@description('Region allowed by your subscription policy (check before deploying).')
param location string = resourceGroup().location

@description('Short, unique project prefix, e.g. kacy-dev')
param namePrefix string = 'kacy'

resource foundry 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: '${namePrefix}-foundry'
  location: location
  kind: 'AIServices'
  sku: {
    name: 'S0'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    customSubDomainName: '${namePrefix}-foundry'
  }
}

resource search 'Microsoft.Search/searchServices@2024-06-01-preview' = {
  name: '${namePrefix}-search'
  location: location
  sku: {
    name: 'free'
  }
  properties: {
    replicaCount: 1
    partitionCount: 1
  }
}

resource contentSafety 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: '${namePrefix}-contentsafety'
  location: location
  kind: 'ContentSafety'
  sku: {
    name: 'F0'
  }
  properties: {
    customSubDomainName: '${namePrefix}-contentsafety'
  }
}

resource language 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: '${namePrefix}-language'
  location: location
  kind: 'TextAnalytics'
  sku: {
    name: 'F0'
  }
  properties: {
    customSubDomainName: '${namePrefix}-language'
  }
}

resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' = {
  name: '${namePrefix}-cosmos'
  location: location
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    locations: [
      {
        locationName: location
        failoverPriority: 0
      }
    ]
    capabilities: [
      {
        name: 'EnableServerless'
      }
    ]
  }
}

output foundryEndpoint string = foundry.properties.endpoint
output searchEndpoint string = 'https://${search.name}.search.windows.net'
output contentSafetyEndpoint string = contentSafety.properties.endpoint
output languageEndpoint string = language.properties.endpoint
output cosmosEndpoint string = cosmos.properties.documentEndpoint
