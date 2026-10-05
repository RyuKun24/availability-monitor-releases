const { spawn } = require('child_process');
const path = require('path');
const { app } = require('electron');
const http = require('http');
const os = require('os');

class PythonManager {
    constructor() {
        this.process = null;
        this.port = 8000;
        this.running = false;
        this.stdout = '';
        this.stderr = '';
    }

    isRunning() {
        return this.running;
    }

    async start() {
        return new Promise((resolve, reject) => {
            try {
                if (this.running) {
                    resolve({ success: true, port: this.port });
                    return;
                }

                // Get path to Python executable
                const execPath = this.getExecutablePath();
                console.log(`Starting Python: ${execPath}`);

                // Start with --web mode for dashboard
                const args = app.isPackaged
                    ? ['--web', '--host', '127.0.0.1', '--port', String(this.port)]
                    : ['main.py', '--web', '--host', '127.0.0.1', '--port', String(this.port)];
                this.process = spawn(execPath, args, {
                    cwd: app.isPackaged ? app.getPath('userData') : path.join(__dirname, '..', '..'),
                    stdio: ['ignore', 'pipe', 'pipe']
                });

                this.running = true;

                // Capture output
                this.process.stdout.on('data', (data) => {
                    const message = data.toString();
                    this.stdout += message;
                    console.log(`[Python] ${message}`);
                });

                this.process.stderr.on('data', (data) => {
                    const message = data.toString();
                    this.stderr += message;
                    console.error(`[Python Error] ${message}`);
                });

                this.process.on('error', (error) => {
                    console.error(`Process error: ${error}`);
                    this.running = false;
                    reject(error);
                });

                this.process.on('exit', (code) => {
                    console.log(`Python process exited with code ${code}`);
                    this.running = false;
                    const tail = (this.stderr || this.stdout).trim().split('\n').slice(-8).join('\n');
                    reject(new Error(`Backend exited with code ${code} before it was ready.${tail ? '\n' + tail : ''} Log: ${path.join(app.getPath('userData'), 'backend.log')}`));
                });

                // Wait for server to be ready
                this.waitForServer(this.port, 90000)
                    .then(() => {
                        resolve({ success: true, port: this.port });
                    })
                    .catch((error) => {
                        this.stop();
                        reject(error);
                    });

            } catch (error) {
                console.error(`Failed to start Python: ${error}`);
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

            this.process.kill('SIGTERM');

            // Wait a bit for graceful shutdown
            setTimeout(() => {
                if (this.process && !this.process.killed) {
                    this.process.kill('SIGKILL');
                }
                this.running = false;
                resolve({ success: true });
            }, 2000);
        });
    }

    async waitForServer(port, timeout = 30000) {
        const startTime = Date.now();

        return new Promise((resolve, reject) => {
            const checkConnection = () => {
                const req = http.get(`http://127.0.0.1:${port}/api/status`, (res) => {
                    if (res.statusCode === 200) {
                        console.log(`Server ready on port ${port}`);
                        resolve();
                    } else {
                        retry();
                    }
                });

                req.on('error', retry);
                req.setTimeout(1000);
            };

            const retry = () => {
                if (!this.running) {
                    reject(new Error('Backend process is not running'));
                } else if (Date.now() - startTime > timeout) {
                    reject(new Error(`Server did not start within ${timeout}ms`));
                } else {
                    setTimeout(checkConnection, 500);
                }
            };

            checkConnection();
        });
    }

    getExecutablePath() {
        // If running PyInstaller-compiled version
        if (app.isPackaged) {
            const resourcesPath = process.resourcesPath || path.join(app.getAppPath(), '..');
            return path.join(resourcesPath, 'availability_monitor.exe');
        }

        // Development: use Python from virtual environment or system
        if (process.platform === 'win32') {
            const venvPython = path.join(__dirname, '..', '..', '.venv', 'Scripts', 'python.exe');
            return venvPython;
        }

        return 'python3';
    }

    async getSettings() {
        try {
            const response = await this.httpGet(`http://127.0.0.1:${this.port}/api/settings`);
            return response;
        } catch (error) {
            throw new Error(`Failed to get settings: ${error.message}`);
        }
    }

    async updateSettings(settings) {
        try {
            const response = await this.httpPost(`http://127.0.0.1:${this.port}/api/settings`, settings);
            return response;
        } catch (error) {
            throw new Error(`Failed to update settings: ${error.message}`);
        }
    }

    httpGet(url) {
        return new Promise((resolve, reject) => {
            http.get(url, (res) => {
                let data = '';
                res.on('data', (chunk) => { data += chunk; });
                res.on('end', () => {
                    try {
                        resolve(JSON.parse(data));
                    } catch (e) {
                        reject(e);
                    }
                });
            }).on('error', reject);
        });
    }

    httpPost(url, data) {
        return new Promise((resolve, reject) => {
            const postData = JSON.stringify(data);
            const urlObj = new URL(url);

            const options = {
                hostname: urlObj.hostname,
                port: urlObj.port,
                path: urlObj.pathname + urlObj.search,
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Content-Length': Buffer.byteLength(postData)
                }
            };

            const req = http.request(options, (res) => {
                let responseData = '';
                res.on('data', (chunk) => { responseData += chunk; });
                res.on('end', () => {
                    try {
                        resolve(JSON.parse(responseData));
                    } catch (e) {
                        reject(e);
                    }
                });
            });

            req.on('error', reject);
            req.write(postData);
            req.end();
        });
    }
}

module.exports = { PythonManager };
