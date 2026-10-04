import type { PluginSDK } from '@ervisio/plugin-sdk';

export type { PluginSDK };

let currentSdk: PluginSDK | undefined;

export function setSdk(sdk: PluginSDK): void {
  currentSdk = sdk;
}

export function getSdk(): PluginSDK {
  if (!currentSdk) {
    throw new Error('PluginSDK not initialized');
  }
  return currentSdk;
}
