document.addEventListener('DOMContentLoaded', function () {
    const formularios = [
        {
            id: 'form-upload-ia-receita',
            botao: 'btn-submit-ia-receita',
            texto: '<i class="bi bi-stars"></i> Interpretar Nota com IA'
        },
        {
            id: 'form-upload-ia-despesa',
            botao: 'btn-submit-ia-despesa',
            texto: '<i class="bi bi-stars"></i> Interpretar Nota com IA'
        }
    ];

    const URL_STATUS = '/ia/status/__ID__';

    // Reduz fotos no dispositivo antes do envio para poupar dados e acelerar a resposta do servidor
    async function reduzirImagem(file, maxLado = 1280, qualidade = 0.85) {
        if (!file.type || !file.type.startsWith('image/')) return file;

        try {
            const bmp = await createImageBitmap(file, { imageOrientation: 'from-image' });
            const escala = Math.min(1, maxLado / Math.max(bmp.width, bmp.height));
            const canvas = document.createElement('canvas');
            canvas.width = Math.round(bmp.width * escala);
            canvas.height = Math.round(bmp.height * escala);
            canvas.getContext('2d').drawImage(bmp, 0, 0, canvas.width, canvas.height);
            bmp.close();

            const blob = await new Promise(ok => canvas.toBlob(ok, 'image/jpeg', qualidade));
            if (!blob) return file;

            const nome = file.name.replace(/\.[^.]+$/, '') + '.jpg';
            return new File([blob], nome, { type: 'image/jpeg' });
        } catch (e) {
            console.warn('IA UPLOAD | Não foi possível otimizar imagem no cliente; enviando original.', e);
            return file;
        }
    }

    formularios.forEach(function (config) {
        const form = document.getElementById(config.id);
        if (!form) return;

        const botao = document.getElementById(config.botao);
        const input = form.querySelector('input[type="file"]');

        if (!botao || !input) return;

        let enviando = false;

        form.addEventListener('submit', async function (event) {
            event.preventDefault();

            if (enviando) return;

            if (!input.files || !input.files[0]) {
                alert('Selecione um arquivo antes de continuar.');
                return;
            }

            const csrf = form.querySelector('input[name="csrf_token"]');

            if (!csrf || !csrf.value) {
                alert('Token de segurança ausente. Atualize a página.');
                return;
            }

            enviando = true;
            botao.disabled = true;
            botao.innerHTML = '<i class="bi bi-arrow-repeat ia-gira"></i> Otimizando e enviando...';

            try {
                const dadosFormulario = new FormData(form);
                const arquivoOtimizado = await reduzirImagem(input.files[0]);
                dadosFormulario.set('documento', arquivoOtimizado, arquivoOtimizado.name);

                const resposta = await fetch(form.action, {
                    method: 'POST',
                    body: dadosFormulario,
                    headers: { 'X-Requested-With': 'XMLHttpRequest' },
                    credentials: 'same-origin'
                });

                let dados;
                try {
                    dados = await resposta.json();
                } catch (_) {
                    throw new Error('O servidor retornou uma resposta inválida.');
                }

                if (!resposta.ok || !dados || !dados.job_id) {
                    throw new Error(
                        dados && (dados.msg || dados.erro)
                            ? (dados.msg || dados.erro)
                            : 'Falha no envio (HTTP ' + resposta.status + ').'
                    );
                }

                botao.innerHTML = '<i class="bi bi-arrow-repeat ia-gira"></i> Processando documento fiscal...';

                const inicio = Date.now();

                while (Date.now() - inicio < 5 * 60 * 1000) {
                    await new Promise(resolve => setTimeout(resolve, 2000));

                    const statusResposta = await fetch(
                        URL_STATUS.replace('__ID__', encodeURIComponent(dados.job_id)),
                        {
                            credentials: 'same-origin',
                            cache: 'no-store'
                        }
                    );

                    if (statusResposta.status === 404) {
                        throw new Error('Processamento não encontrado. Envie o documento novamente.');
                    }

                    if (!statusResposta.ok) continue;

                    const status = await statusResposta.json();

                    if (status.status === 'ok') {
                        window.location.href = status.redirect;
                        return;
                    }

                    if (status.status === 'erro') {
                        throw new Error(status.msg || 'Não foi possível processar o documento.');
                    }
                }

                throw new Error('O processamento demorou demais. Tente novamente.');

            } catch (erro) {
                console.error('IA UPLOAD |', erro);
                alert(erro.message || 'Erro ao processar o documento.');
            } finally {
                enviando = false;
                botao.disabled = false;
                botao.innerHTML = config.texto;
            }
        });
    });
});
