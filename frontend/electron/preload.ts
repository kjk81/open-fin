import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("electronAPI", {
  getBackendStatus: (): Promise<{ running: boolean }> =>
    ipcRenderer.invoke("get-backend-status"),
  stopBackend: (): Promise<{ stopped: boolean }> =>
    ipcRenderer.invoke("stop-backend"),
});
