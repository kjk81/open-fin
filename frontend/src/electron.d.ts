export {};

declare global {
  interface Window {
    electronAPI: {
      getBackendStatus: () => Promise<{ running: boolean }>;
      getWorkerStatus: () => Promise<{ running: boolean }>;
      stopBackend: () => Promise<{ stopped: boolean }>;
      getBackendPort: () => Promise<number>;
      minimizeWindow: () => Promise<void>;
      toggleMaximizeWindow: () => Promise<boolean>;
      closeWindow: () => Promise<void>;
      wipeUserData: () => Promise<{ success: boolean; error?: string }>;
      saveRunBundle: (payload: { defaultPath: string; contents: string }) => Promise<{
        canceled: boolean;
        path?: string;
        error?: string;
      }>;
    };
  }
}
