const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
    // Status
    getStatus: () => ipcRenderer.invoke('get-status'),
    getAppVersion: () => ipcRenderer.invoke('get-app-version'),

    // Python backend control
    startPython: () => ipcRenderer.invoke('start-python'),
    stopPython: () => ipcRenderer.invoke('stop-python'),

    // Chrome control
    openChrome: () => ipcRenderer.invoke('open-chrome'),
    closeChrome: () => ipcRenderer.invoke('close-chrome'),
    openStoreLogins: () => ipcRenderer.invoke('open-store-logins'),

    // Dashboard
    openDashboard: () => ipcRenderer.invoke('open-dashboard'),

    // Settings
    getSettings: () => ipcRenderer.invoke('get-settings'),
    updateSettings: (settings) => ipcRenderer.invoke('update-settings', settings),

    // Updates
    checkUpdates: () => ipcRenderer.invoke('check-updates'),
    installUpdate: () => ipcRenderer.invoke('install-update'),

    // Event listeners
    onUpdateAvailable: (callback) => ipcRenderer.on('update-available', callback),
    onUpdateDownloaded: (callback) => ipcRenderer.on('update-downloaded', callback),

    // Remove listeners
    removeUpdateAvailable: () => ipcRenderer.removeAllListeners('update-available'),
    removeUpdateDownloaded: () => ipcRenderer.removeAllListeners('update-downloaded')
});
