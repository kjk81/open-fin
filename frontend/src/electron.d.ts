export {};

declare global {
  interface Window {
    electronAPI: {
      getBackendStatus: () => Promise<{ running: boolean }>;
      stopBackend: () => Promise<{ stopped: boolean }>;
    };
  }
}
