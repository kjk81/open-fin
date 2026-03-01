import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("electronAPI", {
  getBackendStatus: (): Promise<{ running: boolean }> =>
    ipcRenderer.invoke("get-backend-status"),
  getWorkerStatus: (): Promise<{ running: boolean }> =>
    ipcRenderer.invoke("get-worker-status"),
  stopBackend: (): Promise<{ stopped: boolean }> =>
    ipcRenderer.invoke("stop-backend"),
  getBackendPort: (): Promise<number> =>
    ipcRenderer.invoke("get-backend-port"),
  minimizeWindow: (): Promise<void> =>
    ipcRenderer.invoke("window-minimize"),
  toggleMaximizeWindow: (): Promise<boolean> =>
    ipcRenderer.invoke("window-maximize-toggle"),
  closeWindow: (): Promise<void> =>
    ipcRenderer.invoke("window-close"),
});
