from app import create_app, db
from app.models.categoria import Categoria

app = create_app()

def seed_categorias():
    #Insere categorias padrão se o banco estiver vazio
    if Categoria.query.count() == 0:
        categorias_padrao = [
            'Ingredientes',
            'Embalagens',
            'Transporte',
            'Contas Fixas',
            'Equipamentos',
            'Marketing',
            'Outros'
        ]
        for nome in categorias_padrao:
            db.session.add(Categoria(nome_categoria=nome))
        db.session.commit()
        print('Categorias padrao criadas.')
    # Fecha a sessão após o seed para liberar a conexão com o banco
    db.session.remove()


if __name__ == '__main__':
    with app.app_context():
        seed_categorias()
    app.run(debug=True)
