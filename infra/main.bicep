
targetScope = 'resourceGroup'

// ============================================================
// CloudDrop - Complete Azure infrastructure
// ============================================================

@description('Azure region for CloudDrop')
param location string = resourceGroup().location

@description('Unique lowercase Storage Account name, 3-24 characters')
@minLength(3)
@maxLength(24)
param storageAccountName string

@description('Unique lowercase Cosmos DB account name')
@minLength(3)
@maxLength(44)
param cosmosAccountName string

@description('Unique Function App name')
param functionAppName string

@description('Unique API Management service name')
param apiManagementName string

@description('Email address for the API Management publisher')
param apimPublisherEmail string

@description('API Management publisher name')
param apimPublisherName string = 'CloudDrop'

// ------------------------------------------------------------
// 1. AZURE STORAGE ACCOUNT
// ------------------------------------------------------------

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    accessTier: 'Hot'
    allowBlobPublicAccess: false
    supportsHttpsTrafficOnly: true
    minimumTlsVersion: 'TLS1_2'
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storageAccount
  name: 'default'
  properties: {
    cors: {
      corsRules: [
        {
          allowedOrigins: [
            '*'
          ]
          allowedMethods: [
            'GET'
            'PUT'
            'HEAD'
          ]
          allowedHeaders: [
            '*'
          ]
          exposedHeaders: [
            'ETag'
            'x-ms-request-id'
          ]
          maxAgeInSeconds: 3600
        }
      ]
    }
  }
}

resource filesContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'files'
  properties: {
    publicAccess: 'None'
  }
}

// Delete blobs after more than one day since their last modification.
// Lifecycle deletion is asynchronous and is not an exact expiry timer.
resource lifecyclePolicy 'Microsoft.Storage/storageAccounts/managementPolicies@2021-09-01' = {
  parent: storageAccount
  name: 'default'
  properties: {
    policy: {
      rules: [
        {
          enabled: true
          name: 'delete-old-files'
          type: 'Lifecycle'
          definition: {
            filters: {
                blobTypes: [
                    'blockBlob'
                ]
                prefixMatch: [
                    'files/'
                ]
            }
            actions: {
              baseBlob: {
                delete: {
                  daysAfterModificationGreaterThan: 1
                }
              }
            }
          }
        }
      ]
    }
  }
}

// ------------------------------------------------------------
// 2. COSMOS DB FOR NOSQL
// ------------------------------------------------------------

resource cosmosAccount 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' = {
  name: cosmosAccountName
  location: location
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    capabilities: [
      {
        name: 'EnableServerless'
      }
    ]
    locations: [
      {
        locationName: location
        failoverPriority: 0
        isZoneRedundant: false
      }
    ]
    consistencyPolicy: {
      defaultConsistencyLevel: 'Session'
    }
    enableAutomaticFailover: true
    publicNetworkAccess: 'Enabled'
  }
}

resource cosmosDatabase 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-05-15' = {
  parent: cosmosAccount
  name: 'CloudDropDB'
  properties: {
    resource: {
      id: 'CloudDropDB'
    }
  }
}

resource metadataContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: cosmosDatabase
  name: 'FileMetadata'
  properties: {
    resource: {
      id: 'FileMetadata'
      partitionKey: {
        paths: [
          '/id'
        ]
        kind: 'Hash'
      }
      defaultTtl: -1
    }
  }
}

// Allow the Function App's system-assigned identity to read and write Cosmos DB data.
resource cosmosDataRoleAssignment 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15' = {
  parent: cosmosAccount
  name: guid(cosmosAccount.id, functionApp.name, 'cosmos-data-contributor')
  properties: {
    principalId: functionApp.identity.principalId
    roleDefinitionId: '${cosmosAccount.id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002'
    scope: cosmosAccount.id
  }
}

// ------------------------------------------------------------
// 3. APPLICATION INSIGHTS
// ------------------------------------------------------------

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: '${functionAppName}-insights'
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    RetentionInDays: 30
  }
}

// ------------------------------------------------------------
// 4. AZURE FUNCTIONS CONSUMPTION PLAN
// ------------------------------------------------------------

resource functionPlan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: '${functionAppName}-plan'
  location: location
  kind: 'functionapp'
  sku: {
    name: 'Y1'
    tier: 'Dynamic'
  }
  properties: {
    reserved: true
  }
}

// ------------------------------------------------------------
// 5. AZURE FUNCTION APP
// ------------------------------------------------------------

resource functionApp 'Microsoft.Web/sites@2023-12-01' = {
  name: functionAppName
  location: location
  kind: 'functionapp,linux'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: functionPlan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'Python|3.11'
      minTlsVersion: '1.2'
      ftpsState: 'Disabled'
      cors: {
        allowedOrigins: [
          '*'
        ]
      }
      appSettings: [
        {
          name: 'FUNCTIONS_EXTENSION_VERSION'
          value: '~4'
        }
        {
          name: 'FUNCTIONS_WORKER_RUNTIME'
          value: 'python'
        }
        {
          name: 'SCM_DO_BUILD_DURING_DEPLOYMENT'
          value: 'true'
        }
        {
          name: 'ENABLE_ORYX_BUILD'
          value: 'true'
        }
        {
          name: 'AzureWebJobsStorage'
          value: 'DefaultEndpointsProtocol=https;AccountName=${storageAccount.name};AccountKey=${storageAccount.listKeys().keys[0].value};EndpointSuffix=${environment().suffixes.storage}'
        }
        {
          name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
          value: appInsights.properties.ConnectionString
        }
        {
          name: 'COSMOS_ENDPOINT'
          value: cosmosAccount.properties.documentEndpoint
        }
        {
          name: 'COSMOS_DATABASE'
          value: cosmosDatabase.name
        }
        {
          name: 'COSMOS_CONTAINER'
          value: metadataContainer.name
        }
        {
          name: 'STORAGE_ACCOUNT_NAME'
          value: storageAccount.name
        }
        {
          name: 'BLOB_CONTAINER_NAME'
          value: filesContainer.name
        }
        {
          name: 'FRONTEND_ORIGIN'
          value: '*'
        }
      ]
    }
  }
}

// ------------------------------------------------------------
// 6. API MANAGEMENT - CONSUMPTION TIER
// ------------------------------------------------------------

resource apiManagement 'Microsoft.ApiManagement/service@2023-05-01-preview' = {
  name: apiManagementName
  location: location
  sku: {
    name: 'Consumption'
    capacity: 0
  }
  properties: {
    publisherEmail: apimPublisherEmail
    publisherName: apimPublisherName
    publicNetworkAccess: 'Enabled'
    legacyPortalStatus: 'Disabled'
    customProperties: {
      'Microsoft.WindowsAzure.ApiManagement.Gateway.Protocols.Server.Http2': 'False'
      'Microsoft.WindowsAzure.ApiManagement.Gateway.Security.Backend.Protocols.Ssl30': 'False'
      'Microsoft.WindowsAzure.ApiManagement.Gateway.Security.Backend.Protocols.Tls10': 'False'
      'Microsoft.WindowsAzure.ApiManagement.Gateway.Security.Backend.Protocols.Tls11': 'False'
      'Microsoft.WindowsAzure.ApiManagement.Gateway.Security.Protocols.Tls10': 'False'
      'Microsoft.WindowsAzure.ApiManagement.Gateway.Security.Protocols.Tls11': 'False'
    }
  }
}

// ------------------------------------------------------------
// OUTPUTS
// ------------------------------------------------------------

output storageAccountName string = storageAccount.name
output blobContainerName string = filesContainer.name

output cosmosAccountName string = cosmosAccount.name
output cosmosDatabaseName string = cosmosDatabase.name
output cosmosContainerName string = metadataContainer.name

output functionAppName string = functionApp.name
output functionAppUrl string = 'https://${functionApp.properties.defaultHostName}'

output apiManagementName string = apiManagement.name
output appInsightsName string = appInsights.name
