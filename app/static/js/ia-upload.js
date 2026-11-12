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
            botao.innerHTML = '<i class="bi bi-arrow-repeat ia-gira"></i> Enviando documento...';

            try {
                const dadosFormulario = new FormData(form);

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

                botao.innerHTML = '<i class="bi bi-arrow-repeat ia-gira"></i> Lendo documento...';

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
                enviando = false;
                botao.disabled = false;
                botao.innerHTML = config.texto;
            }
        });
    });
});