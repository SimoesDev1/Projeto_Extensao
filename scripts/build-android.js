/**
 * Build Script para Android / Capacitor
 * 
 * 1. Resolve a URL da API da variável de ambiente API_URL ou CAPACITOR_SERVER_URL,
 *    utilizando '[URL_PUBLICA_DO_BACKEND]' como fallback para o APK.
 * 2. Atualiza o capacitor.config.json com a URL configurada.
 * 3. Valida e configura JAVA_HOME e ANDROID_HOME / local.properties.
 * 4. Executa o Capacitor Sync para sincronizar os assets e configurações nativas.
 * 5. Compila o APK de desenvolvimento (assembleDebug).
 */

const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

const projectRoot = path.resolve(__dirname, '..');
const envFilePath = path.join(projectRoot, '.env');
const capacitorConfigPath = path.join(projectRoot, 'capacitor.config.json');

// Função simples para carregar variáveis do .env sem dependência extra
function loadEnv() {
    const env = {};
    if (fs.existsSync(envFilePath)) {
        const content = fs.readFileSync(envFilePath, 'utf-8');
        content.split(/\r?\n/).forEach(line => {
            const trimmed = line.trim();
            if (trimmed && !trimmed.startsWith('#')) {
                const eqIdx = trimmed.indexOf('=');
                if (eqIdx !== -1) {
                    const key = trimmed.substring(0, eqIdx).trim();
                    let val = trimmed.substring(eqIdx + 1).trim();
                    if ((val.startsWith('"') && val.endsWith('"')) || (val.startsWith("'") && val.endsWith("'"))) {
                        val = val.slice(1, -1);
                    }
                    env[key] = val;
                }
            }
        });
    }
    return env;
}

// Resolução inteligente do JAVA_HOME
function resolveJavaHome() {
    let current = process.env.JAVA_HOME;
    if (current && fs.existsSync(current)) {
        return current;
    }
    const candidates = [
        'C:\\Program Files\\Microsoft\\jdk-17.0.20.8-hotspot',
        'C:\\Program Files\\Eclipse Adoptium\\jdk-17.0.20.8-hotspot',
        'C:\\Program Files\\Android\\Android Studio\\jbr',
        'C:\\Program Files\\Java\\jdk-17'
    ];
    for (const cand of candidates) {
        if (fs.existsSync(cand)) {
            return cand;
        }
    }
    try {
        const out = execSync('where java', { encoding: 'utf-8' }).trim().split(/\r?\n/)[0];
        if (out) {
            const binDir = path.dirname(out);
            const jdkDir = path.dirname(binDir);
            if (fs.existsSync(jdkDir)) return jdkDir;
        }
    } catch (e) {}
    return current;
}

const fileEnv = loadEnv();
const apiUrl = process.env.API_URL || fileEnv.API_URL || process.env.CAPACITOR_SERVER_URL || fileEnv.CAPACITOR_SERVER_URL || '[URL_PUBLICA_DO_BACKEND]';
const appId = process.env.CAPACITOR_APP_ID || fileEnv.CAPACITOR_APP_ID || 'com.exemplo.app';
const appName = process.env.CAPACITOR_APP_NAME || fileEnv.CAPACITOR_APP_NAME || 'Nome do App';

console.log('==================================================');
console.log('   Build Android APK - Doce como Mel (Capacitor)');
console.log('==================================================');
console.log(`- App ID: ${appId}`);
console.log(`- App Name: ${appName}`);
console.log(`- API URL (Backend): ${apiUrl}`);

if (!apiUrl || apiUrl === '[URL_PUBLICA_DO_BACKEND]' || apiUrl.includes('[URL_PUBLICA_DO_BACKEND]')) {
    console.error('\n❌ ERRO: A URL da API do backend não foi configurada!');
    console.error('O build não pode continuar utilizando o placeholder [URL_PUBLICA_DO_BACKEND].');
    console.error('Defina a variável API_URL no seu arquivo .env ou informe-a no ambiente antes de gerar o APK.');
    console.error('Exemplo no .env: API_URL=https://meu-app.onrender.com\n');
    process.exit(1);
}

// 1. Configuração do ambiente Java
const validJavaHome = resolveJavaHome();
if (validJavaHome) {
    process.env.JAVA_HOME = validJavaHome;
    console.log(`✔ JAVA_HOME detectado: ${validJavaHome}`);
}

// 2. Atualiza capacitor.config.json
let config = {};
if (fs.existsSync(capacitorConfigPath)) {
    try {
        config = JSON.parse(fs.readFileSync(capacitorConfigPath, 'utf-8'));
    } catch (e) {
        config = {};
    }
}

config.appId = appId;
config.appName = appName;
config.webDir = 'www';
config.server = config.server || {};
config.server.url = apiUrl;
if (apiUrl.startsWith('http://')) {
    config.server.cleartext = true;
} else {
    delete config.server.cleartext;
}
config.server.androidScheme = 'https';
if (config.android) {
    delete config.android.allowMixedContent;
}

fs.writeFileSync(capacitorConfigPath, JSON.stringify(config, null, 2), 'utf-8');
console.log('✔ capacitor.config.json atualizado.');

// 3. Garante pasta www e index.html
const wwwDir = path.join(projectRoot, 'www');
if (!fs.existsSync(wwwDir)) {
    fs.mkdirSync(wwwDir, { recursive: true });
}
const wwwIndex = path.join(wwwDir, 'index.html');
if (!fs.existsSync(wwwIndex)) {
    fs.writeFileSync(wwwIndex, '<!DOCTYPE html><html><body><h1>Doce como Mel</h1></body></html>');
}

// 4. Garante android/local.properties
const androidDir = path.join(projectRoot, 'android');
const localPropsPath = path.join(androidDir, 'local.properties');
const androidHome = process.env.ANDROID_HOME || process.env.ANDROID_SDK_ROOT || 'C:\\Users\\mathe\\AppData\\Local\\Android\\Sdk';
if (!fs.existsSync(localPropsPath) && fs.existsSync(androidDir)) {
    const escapedHome = androidHome.replace(/\\/g, '\\\\').replace(/:/g, '\\:');
    fs.writeFileSync(localPropsPath, `sdk.dir=${escapedHome}\n`, 'utf-8');
    console.log(`✔ android/local.properties configurado com SDK: ${androidHome}`);
}

// Garante android.overridePathCheck=true no gradle.properties (para diretórios com acentos/espaços no Windows)
const gradlePropsPath = path.join(androidDir, 'gradle.properties');
if (fs.existsSync(gradlePropsPath)) {
    const props = fs.readFileSync(gradlePropsPath, 'utf-8');
    if (!props.includes('android.overridePathCheck=true')) {
        fs.appendFileSync(gradlePropsPath, '\nandroid.overridePathCheck=true\n');
    }
}

// 5. Sincroniza o Capacitor
console.log('\n--- Executando Capacitor Sync ---');
try {
    execSync('npx cap sync android', { cwd: projectRoot, stdio: 'inherit', env: process.env });
    console.log('✔ Capacitor sync concluído.');
} catch (error) {
    console.error('✖ Erro ao sincronizar o Capacitor:', error.message);
    process.exit(1);
}

// 6. Compilação do APK com Gradle
console.log('\n--- Compilando APK Android (assembleDebug) ---');
const isWindows = process.platform === 'win32';
const gradlewCmd = isWindows ? 'gradlew.bat' : './gradlew';

try {
    execSync(`${gradlewCmd} assembleDebug`, { cwd: androidDir, stdio: 'inherit', env: process.env });
    console.log('✔ Compilação Gradle finalizada.');
} catch (error) {
    console.error('✖ Erro durante a compilação Gradle:', error.message);
    process.exit(1);
}

// 7. Localização do arquivo APK gerado
const apkPath = path.join(androidDir, 'app', 'build', 'outputs', 'apk', 'debug', 'app-debug.apk');
if (fs.existsSync(apkPath)) {
    const stats = fs.statSync(apkPath);
    const sizeMb = (stats.size / (1024 * 1024)).toFixed(2);
    console.log('\n==================================================');
    console.log('✔ APK GERADO COM SUCESSO!');
    console.log(`- Caminho: ${apkPath}`);
    console.log(`- Tamanho: ${sizeMb} MB`);
    console.log('==================================================');
} else {
    console.log('\n[AVISO] Compilação concluída, mas o APK não foi encontrado no caminho padrão: ' + apkPath);
}
