const { spawn } = require('child_process');
const path = require('path');
const os = require('os');
const fs = require('fs');
const net = require('net');

class ChromeManager {
    constructor() {
        this.process = null;
        this.debugPort = 9222;
        this.running = false;
    }

    isRunning() {
        return this.running;
    }

    async start() {
        return new Promise((resolve, reject) => {
            try {
                if (this.running) {
                    resolve({ success: true });
                    return;
                }

                // Find Chrome executable
                const chromePath = this.findChromePath();
                if (!chromePath) {
                    reject(new Error('Chrome not found. Please install Google Chrome.'));
                    return;
                }

                console.log(`Starting Chrome: ${chromePath}`);

                // Create user data directory for session persistence
                const userDataDir = path.join(os.homedir(), '.availability-monitor', 'chrome-profile');
                if (!fs.existsSync(userDataDir)) {
                    fs.mkdirSync(userDataDir, { recursive: true });
                }

                // Start Chrome with debug port
                this.process = spawn(chromePath, [
                    `--remote-debugging-port=${this.debugPort}`,
                    `--user-data-dir=${userDataDir}`,
                    '--disable-extensions',
                    '--disable-default-apps',
                    '--no-first-run'
                ], {
                    stdio: 'ignore',
                    detached: true
                });

                this.running = true;

                this.process.on('error', (error) => {
                    console.error(`Chrome process error: ${error}`);
                    this.running = false;
                    reject(error);
                });

                // Give Chrome a moment to start
                setTimeout(() => {
                    this.checkConnection()
                        .then(() => {
                            console.log(`Chrome started on port ${this.debugPort}`);
                            resolve({ success: true });
                        })
                        .catch((error) => {
                            reject(error);
                        });
                }, 1000);

            } catch (error) {
                console.error(`Failed to start Chrome: ${error}`);
                reject(error);
            }
        });
    }

    async stop() {
        return new Promise((resolve) => {
            if (!this.running || !this.process) {
                resolve({ success: true });
                return;
            }

            // Kill Chrome process
            try {
                if (process.platform === 'win32') {
                    // On Windows, kill by PID
                    if (this.process.pid) {
                        spawn('taskkill', ['/PID', String(this.process.pid), '/F']);
                    }
                } else {
                    this.process.kill('SIGTERM');
                    setTimeout(() => {
                        if (this.process && !this.process.killed) {
                            this.process.kill('SIGKILL');
                        }
                    }, 1000);
                }
            } catch (error) {
                console.error(`Error killing Chrome: ${error}`);
            }

            this.running = false;
            setTimeout(() => resolve({ success: true }), 500);
        });
    }

    findChromePath() {
        const possiblePaths = [];

        if (process.platform === 'win32') {
            possiblePaths.push(
                path.join(process.env.ProgramFiles, 'Google', 'Chrome', 'Application', 'chrome.exe'),
                path.join(process.env['ProgramFiles(x86)'], 'Google', 'Chrome', 'Application', 'chrome.exe'),
                path.join(process.env.LOCALAPPDATA, 'Google', 'Chrome', 'Application', 'chrome.exe')
            );
        } else if (process.platform === 'darwin') {
            possiblePaths.push(
                '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
            );
        } else {
            possiblePaths.push(
                '/usr/bin/google-chrome',
                '/usr/bin/chromium-browser',
                '/snap/bin/chromium'
            );
        }

        for (const chromePath of possiblePaths) {
            if (fs.existsSync(chromePath)) {
                return chromePath;
            }
        }

        return null;
    }

    async checkConnection() {
        return new Promise((resolve, reject) => {
            const socket = net.createConnection({
                host: 'localhost',
                port: this.debugPort,
                timeout: 5000
            });

            socket.on('connect', () => {
                socket.destroy();
                resolve();
            });

            socket.on('error', (error) => {
                reject(new Error(`Could not connect to Chrome on port ${this.debugPort}: ${error.message}`));
            });

            socket.on('timeout', () => {
                socket.destroy();
                reject(new Error(`Timeout connecting to Chrome on port ${this.debugPort}`));
            });
        });
    }
}

module.exports = { ChromeManager };
