# Copaiba Lexikon e-LTS(se)

**Copaiba Lexikon** é um editor avançado de arquivos `oto.ini` para os sintetizadores de voz [UTAU](https://utau.wiki/) e [OpenUtau](https://github.com/stakira/OpenUtau), desenvolvido com foco em usabilidade, eficiência e recursos modernos.

Oferece uma interface gráfica intuitiva para ajustar os parâmetros de tempo (Offset, Overlap, Preutter, Consonant, Cutoff) com visualização de forma de onda, mini-mapa, suporte a presets, navegação rápida, edição em lote e aceleração de GPU para processamento de áudio.

### **Sobre a Linha e-LTS(se) da YVYRA**

O **Copaiba Lexikon** faz parte da linha **e-LTS(se)** da iniciativa **YVYRA** (associada ao [Studio Pomar Yvyra](https://github.com/studiopomar)). A sigla **e-LTS(se)** indica que este software é parte de um conjunto de ferramentas consideradas **Estáveis e de Longo Tempo de Suporte (Long-Term Support)**, com foco em confiabilidade e robustez para o usuário final.

Isso significa que:

*   **Estabilidade em Primeiro Lugar:** Priorizo a estabilidade e confiabilidade do software. As versões lançadas são testadas exaustivamente para minimizar bugs e garantir um funcionamento e workflow consistentes.
*   **Processo de Revisão Cuidadoso:** Novas funcionalidades e sugestões de melhoria passam por um processo de revisão detalhado ("pente fino"). Isso garante que as alterações integrem-se bem ao código existente, mantenham a qualidade e não introduzam instabilidades.
*   **Implementações com Planejamento:** Devido à natureza estável da linha, novas implementações podem levar mais tempo para serem incorporadas ao código principal. Esse prazo maior é um investimento na qualidade e segurança da atualização para todos os usuários.
*   **Compromisso com o Usuário:** A e-LTS(se) é voltada para usuários que buscam ferramentas sólidas para uso contínuo, onde a previsibilidade e a confiabilidade do software são mais importantes do que a introdução constante de novas funcionalidades.

Em resumo, ao utilizar o **Copaiba Lexikon**, você está adotando uma ferramenta estável e confiável, parte de um ecossistema comprometido com a excelência e a autonomia tecnológica local, mesmo que isso signifique um ritmo mais conservador na adição de novos recursos.

### Objetivos

*   **Usabilidade:** Interface limpa e eficiente, otimizada para o fluxo de trabalho de editores de voicebank.
*   **Eficiência:** Processamento rápido, inclusive para arquivos grandes, com suporte opcional a GPU.
*   **Manutenibilidade:** Código modular e bem estruturado para facilitar futuras melhorias e contribuições.
*   **Tecnologia Local:** Parte do projeto [Studio Pomar Yvyra](https://github.com/studiopomar) e da iniciativa de ferramentas brasileiras para UTAU.

  ### Recursos

*   **Visualização de Forma de Onda:** Exibe a forma de onda do áudio WAV com grades e zoom/pan.
*   **Mini-Mapa Interativo:** Permite navegar visualmente pela forma de onda completa.
*   **Aceleração de GPU:** Opcionalmente utiliza GPU (CUDA ou OpenCL) para carregar e processar áudio mais rapidamente.
*   **Edição de Parâmetros:** Edita Offset, Overlap, Preutter, Consonant e Cutoff diretamente na forma de onda ou na tabela.
*   **Atalhos de Teclado:** Navegação rápida entre aliases (setas, roda do mouse), ajuste fino com teclas Q/W/E/R/T.
*   **Presets de Parâmetros:** Aplica configurações predefinidas (CV, VCV, VV, etc.) ou cria e gerencia presets personalizados.
*   **Edição em Lote:** Ajusta múltiplos aliases de uma só vez com base em deslocamentos.
*   **Filtro de Aliases:** Pesquise aliases específicos na tabela.
*   **Marcação de Conclusão:** Marque aliases como concluídos e visualize o progresso.
*   **Navegação e Persistência:** Navegação fluida entre aliases com persistência do zoom.
*   **Suporte a Múltiplos Encodings:** Lê e escreve arquivos `oto.ini` em UTF-8, Shift-JIS (cp932) e ANSI (mbcs).
*   **Salvamento Automático:** Opção para salvar automaticamente alterações no `oto.ini` e manter backups.
*   **Interface Moderna:** Estilo Fusion com tema escuro, inspirado em ferramentas profissionais.
*   **Multiplataforma:** Funciona em Windows, Linux e potencialmente macOS (não testado extensivamente).
### Contribuições são bem-vindas, mas:

Sinta-se à vontade para abrir *Issues* para relatar bugs ou sugerir recursos, ou enviar *Pull Requests* para melhorar o código.

1.  Faça um *Fork* do projeto.
2.  Crie um *branch* para sua feature (`git checkout -b feature/NovaFeature`).
3.  Commit suas mudanças (`git commit -m 'Adiciona NovaFeature'`).
4.  Faça *Push* para o *branch* (`git push origin feature/NovaFeature`).
5.  Abra um *Pull Request*.

Por favor, siga o [Código de Conduta](./CODE_OF_CONDUCT.md) (se houver).
