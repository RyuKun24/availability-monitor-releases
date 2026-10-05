const { app, BrowserWindow, Menu, ipcMain, dialog } = require('electron');
const fs = require('fs');
const path = require('path');
const { autoUpdater } = require('electron-updater');
const { PythonManager } = require('./utils/python-manager');
const { ChromeManager } = require('./utils/chrome-manager');
const os = require('os');

let mainWindow;
let pythonManager;
let chromeManager;
let appSettings = null;
let dashboardUrl = 'http://127.0.0.1:8000';

app.on('ready', createWindow);
app.on('window-all-closed', onWindowAllClosed);
app.on('activate', onActivate);

function createWindow() {
    mainWindow = new BrowserWindow({
        width: 600,
        height: 700,
        webPreferences: {
            preload: path.join(__dirname, 'preload.js'),
            nodeIntegration: false,
            contextIsolation: true,
            enableRemoteModule: false
        },
        icon: path.join(__dirname, 'assets', 'icon.png')
    });

    // Load app UI
    mainWindow.loadFile(path.join(__dirname, 'index.html'));

    // Initialize managers
    pythonManager = new PythonManager();
    chromeManager = new ChromeManager();

    // Open DevTools if in development
    if (process.env.NODE_ENV === 'development') {
        mainWindow.webContents.openDevTools();
    }

    setupIPC();
    createMenu();

    // Check for updates after the window is ready; updater errors must not stop the launcher.
    checkForUpdatesSafely();
}

function hasUpdateMetadata() {
    if (!app.isPackaged) {
        return false;
    }

    const updateMetadataPath = path.join(process.resourcesPath, 'app-update.yml');
    return fs.existsSync(updateMetadataPath);
}

function checkForUpdatesSafely() {
    if (!app.isPackaged || !hasUpdateMetadata()) {
        return;
    }

    autoUpdater.checkForUpdatesAndNotify().catch((error) => {
        console.error(`Update check failed: ${error.message}`);
    });
}

function createMenu() {
    const template = [
        {
            label: 'File',
            submenu: [
                {
                    label: 'Exit',
                    accelerator: 'CmdOrCtrl+Q',
                    click: () => {
                        app.quit();
                    }
                }
            ]
        },
        {
            label: 'Help',
            submenu: [
                {
                    label: 'About',
                    click: () => {
                        dialog.showMessageBox(mainWindow, {
                            type: 'info',
                            title: 'About Availability Monitor',
                            message: `Availability Monitor v${app.getVersion()}`,
                            detail: 'Monitor product availability across multiple stores and receive Telegram alerts.'
                        });
                    }
                }
            ]
        }
    ];

    Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

function setupIPC() {
    // Get app version
    ipcMain.handle('get-app-version', () => app.getVersion());

    // Get app status
    ipcMain.handle('get-status', async () => {
        return {
            pythonRunning: pythonManager.isRunning(),
            chromeRunning: chromeManager.isRunning(),
            port: pythonManager.port,
            dashboardUrl: dashboardUrl
        };
    });

    // Start Python backend
    ipcMain.handle('start-python', async () => {
        try {
            const result = await pythonManager.start();
            dashboardUrl = `http://127.0.0.1:${result.port}`;
            return {
                success: true,
                port: result.port,
                message: 'Backend started'
            };
        } catch (error) {
            return {
                success: false,
                error: error.message
            };
        }
    });

    // Stop Python backend
    ipcMain.handle('stop-python', async () => {
        try {
            await pythonManager.stop();
            return { success: true, message: 'Backend stopped' };
        } catch (error) {
            return {
                success: false,
                error: error.message
            };
        }
    });

    // Open Chrome debug
    ipcMain.handle('open-chrome', async () => {
        try {
            await chromeManager.start();
            return { success: true, message: 'Chrome opened' };
        } catch (error) {
            return {
                success: false,
                error: error.message
            };
        }
    });

    // Close Chrome
    ipcMain.handle('close-chrome', async () => {
        try {
            await chromeManager.stop();
            return { success: true, message: 'Chrome closed' };
        } catch (error) {
            return {
                success: false,
                error: error.message
            };
        }
    });

    // Open dashboard
    ipcMain.handle('open-dashboard', async () => {
        const { shell } = require('electron');
        try {
            await shell.openExternal(dashboardUrl);
            return { success: true };
        } catch (error) {
            return {
                success: false,
                error: error.message
            };
        }
    });

    // Get settings
    ipcMain.handle('get-settings', async () => {
        try {
            return await pythonManager.getSettings();
        } catch (error) {
            return { error: error.message };
        }
    });

    // Update settings
    ipcMain.handle('update-settings', async (event, settings) => {
        try {
            return await pythonManager.updateSettings(settings);
        } catch (error) {
            return { error: error.message };
        }
    });

    // Check for updates
    ipcMain.handle('check-updates', async () => {
        const currentVersion = app.getVersion();

        if (!app.isPackaged) {
            return {
                updateAvailable: false,
                currentVersion,
                availableVersion: null,
                message: 'Update checks only run in the installed app, not in dev mode.',
                status: 'not_packaged'
            };
        }

        if (!hasUpdateMetadata()) {
            return {
                updateAvailable: false,
                currentVersion,
                availableVersion: null,
                message: 'This build has no update metadata. It is not configured for auto-updates yet.',
                status: 'no_update_config'
            };
        }

        try {
            const result = await autoUpdater.checkForUpdates();
            const availableVersion = result?.updateInfo?.version ?? null;
            const updateAvailable = Boolean(availableVersion && availableVersion !== currentVersion);

            return {
                updateAvailable,
                currentVersion,
                availableVersion,
                status: updateAvailable ? 'update_available' : 'up_to_date',
                message: updateAvailable ? `Version ${availableVersion} is available.` : 'You are running the latest version.'
            };
        } catch (error) {
            return {
                updateAvailable: false,
                currentVersion,
                availableVersion: null,
                status: 'error',
                error: error?.message || String(error),
                message: 'Update check failed.'
            };
        }
    });

    // Download and install update
    ipcMain.handle('install-update', async () => {
        if (!app.isPackaged) {
            return {
                success: false,
                error: 'Update installation only works in the installed app, not in dev mode.'
            };
        }

        try {
            await autoUpdater.downloadUpdate();
            autoUpdater.quitAndInstall();
            return { success: true };
        } catch (error) {
            return {
                success: false,
                error: error.message
            };
        }
    });
}

function onWindowAllClosed() {
    if (pythonManager) {
        pythonManager.stop().catch(console.error);
    }
    if (chromeManager) {
        chromeManager.stop().catch(console.error);
    }

    if (process.platform !== 'darwin') {
        app.quit();
    }
}

function onActivate() {
    if (mainWindow === null) {
        createWindow();
    }
}

// Handle auto-updater events
autoUpdater.on('update-available', () => {
    if (mainWindow) {
        mainWindow.webContents.send('update-available');
    }
});

autoUpdater.on('update-downloaded', () => {
    if (mainWindow) {
        mainWindow.webContents.send('update-downloaded');
    }
});

autoUpdater.on('error', (error) => {
    console.error(`Auto-updater error: ${error.message}`);
});
