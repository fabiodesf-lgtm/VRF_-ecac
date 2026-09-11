// GERADO AUTOMATICAMENTE — não edite à mão.
//
// Regenere com:
//   python3 scripts/gerar_tipos.py "$DATABASE_URL" \
//     > apps/web/lib/database.types.ts

export type Json = string | number | boolean | null | { [key: string]: Json | undefined } | Json[];

export type Database = {
  public: {
    Tables: {
      audit_log: {
        Row: {
          id: number;
          actor_id: string | null;
          actor_tipo: string;
          acao: string;
          entidade: string;
          entidade_id: string | null;
          antes: Json | null;
          depois: Json | null;
          ip: string | null;
          created_at: string;
        };
        Insert: {
          id?: number;
          actor_id?: string | null;
          actor_tipo?: string;
          acao: string;
          entidade: string;
          entidade_id?: string | null;
          antes?: Json | null;
          depois?: Json | null;
          ip?: string | null;
          created_at?: string;
        };
        Update: {
          id?: number;
          actor_id?: string | null;
          actor_tipo?: string;
          acao?: string;
          entidade?: string;
          entidade_id?: string | null;
          antes?: Json | null;
          depois?: Json | null;
          ip?: string | null;
          created_at?: string;
        };
        Relationships: [
          {
            foreignKeyName: "audit_log_actor_id_fkey";
            columns: ["actor_id"];
            isOneToOne: false;
            referencedRelation: "profiles";
            referencedColumns: ["id"];
          },
        ];
      };
      avisos: {
        Row: {
          id: string;
          empresa_id: string;
          marco: Database["public"]["Enums"]["marco"];
          agendado_para: string;
          status: Database["public"]["Enums"]["aviso_status"];
          mensagem_id: string | null;
          tentativas: number;
          erro: string | null;
          enviado_em: string | null;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          empresa_id: string;
          marco: Database["public"]["Enums"]["marco"];
          agendado_para: string;
          status?: Database["public"]["Enums"]["aviso_status"];
          mensagem_id?: string | null;
          tentativas?: number;
          erro?: string | null;
          enviado_em?: string | null;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          empresa_id?: string;
          marco?: Database["public"]["Enums"]["marco"];
          agendado_para?: string;
          status?: Database["public"]["Enums"]["aviso_status"];
          mensagem_id?: string | null;
          tentativas?: number;
          erro?: string | null;
          enviado_em?: string | null;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [
          {
            foreignKeyName: "avisos_empresa_id_fkey";
            columns: ["empresa_id"];
            isOneToOne: false;
            referencedRelation: "empresas";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "avisos_mensagem_fk";
            columns: ["mensagem_id"];
            isOneToOne: false;
            referencedRelation: "mensagens";
            referencedColumns: ["id"];
          },
        ];
      };
      configuracoes: {
        Row: {
          chave: string;
          valor: Json | null;
          valor_cipher: string | null;
          sensivel: boolean;
          descricao: string | null;
          updated_at: string;
          updated_by: string | null;
        };
        Insert: {
          chave: string;
          valor?: Json | null;
          valor_cipher?: string | null;
          sensivel?: boolean;
          descricao?: string | null;
          updated_at?: string;
          updated_by?: string | null;
        };
        Update: {
          chave?: string;
          valor?: Json | null;
          valor_cipher?: string | null;
          sensivel?: boolean;
          descricao?: string | null;
          updated_at?: string;
          updated_by?: string | null;
        };
        Relationships: [
          {
            foreignKeyName: "configuracoes_updated_by_fkey";
            columns: ["updated_by"];
            isOneToOne: false;
            referencedRelation: "profiles";
            referencedColumns: ["id"];
          },
        ];
      };
      conversas: {
        Row: {
          id: string;
          empresa_id: string | null;
          whatsapp: string;
          estado: Database["public"]["Enums"]["conversa_estado"];
          contexto: Json;
          tentativas_invalidas: number;
          expira_em: string | null;
          bot_pausado: boolean;
          pausado_em: string | null;
          pausado_por: string | null;
          ultima_mensagem_em: string | null;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          empresa_id?: string | null;
          whatsapp: string;
          estado?: Database["public"]["Enums"]["conversa_estado"];
          contexto?: Json;
          tentativas_invalidas?: number;
          expira_em?: string | null;
          bot_pausado?: boolean;
          pausado_em?: string | null;
          pausado_por?: string | null;
          ultima_mensagem_em?: string | null;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          empresa_id?: string | null;
          whatsapp?: string;
          estado?: Database["public"]["Enums"]["conversa_estado"];
          contexto?: Json;
          tentativas_invalidas?: number;
          expira_em?: string | null;
          bot_pausado?: boolean;
          pausado_em?: string | null;
          pausado_por?: string | null;
          ultima_mensagem_em?: string | null;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [
          {
            foreignKeyName: "conversas_empresa_id_fkey";
            columns: ["empresa_id"];
            isOneToOne: false;
            referencedRelation: "empresas";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "conversas_pausado_por_fkey";
            columns: ["pausado_por"];
            isOneToOne: false;
            referencedRelation: "profiles";
            referencedColumns: ["id"];
          },
        ];
      };
      darfs: {
        Row: {
          id: string;
          debito_id: string;
          empresa_id: string;
          interacao_id: string | null;
          data_consolidacao: string;
          valor_principal: number | null;
          valor_multa: number | null;
          valor_juros: number | null;
          valor_total: number | null;
          codigo_barras: string | null;
          pdf_storage_path: string | null;
          sicalc_payload: Json;
          status: Database["public"]["Enums"]["darf_status"];
          aprovado_por: string | null;
          aprovado_em: string | null;
          mensagem_id: string | null;
          erro: string | null;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          debito_id: string;
          empresa_id: string;
          interacao_id?: string | null;
          data_consolidacao: string;
          valor_principal?: number | null;
          valor_multa?: number | null;
          valor_juros?: number | null;
          valor_total?: number | null;
          codigo_barras?: string | null;
          pdf_storage_path?: string | null;
          sicalc_payload?: Json;
          status?: Database["public"]["Enums"]["darf_status"];
          aprovado_por?: string | null;
          aprovado_em?: string | null;
          mensagem_id?: string | null;
          erro?: string | null;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          debito_id?: string;
          empresa_id?: string;
          interacao_id?: string | null;
          data_consolidacao?: string;
          valor_principal?: number | null;
          valor_multa?: number | null;
          valor_juros?: number | null;
          valor_total?: number | null;
          codigo_barras?: string | null;
          pdf_storage_path?: string | null;
          sicalc_payload?: Json;
          status?: Database["public"]["Enums"]["darf_status"];
          aprovado_por?: string | null;
          aprovado_em?: string | null;
          mensagem_id?: string | null;
          erro?: string | null;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [
          {
            foreignKeyName: "darfs_aprovado_por_fkey";
            columns: ["aprovado_por"];
            isOneToOne: false;
            referencedRelation: "profiles";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "darfs_debito_id_fkey";
            columns: ["debito_id"];
            isOneToOne: false;
            referencedRelation: "debitos";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "darfs_empresa_id_fkey";
            columns: ["empresa_id"];
            isOneToOne: false;
            referencedRelation: "empresas";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "darfs_interacao_id_fkey";
            columns: ["interacao_id"];
            isOneToOne: false;
            referencedRelation: "interacoes";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "darfs_mensagem_id_fkey";
            columns: ["mensagem_id"];
            isOneToOne: false;
            referencedRelation: "mensagens";
            referencedColumns: ["id"];
          },
        ];
      };
      debito_marcos: {
        Row: {
          id: string;
          debito_id: string;
          empresa_id: string;
          marco: Database["public"]["Enums"]["marco"];
          status: Database["public"]["Enums"]["marco_status"];
          motivo_supressao: string | null;
          aviso_id: string | null;
          agendado_para: string;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          debito_id: string;
          empresa_id: string;
          marco: Database["public"]["Enums"]["marco"];
          status?: Database["public"]["Enums"]["marco_status"];
          motivo_supressao?: string | null;
          aviso_id?: string | null;
          agendado_para: string;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          debito_id?: string;
          empresa_id?: string;
          marco?: Database["public"]["Enums"]["marco"];
          status?: Database["public"]["Enums"]["marco_status"];
          motivo_supressao?: string | null;
          aviso_id?: string | null;
          agendado_para?: string;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [
          {
            foreignKeyName: "debito_marcos_aviso_id_fkey";
            columns: ["aviso_id"];
            isOneToOne: false;
            referencedRelation: "avisos";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "debito_marcos_debito_id_fkey";
            columns: ["debito_id"];
            isOneToOne: false;
            referencedRelation: "debitos";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "debito_marcos_empresa_id_fkey";
            columns: ["empresa_id"];
            isOneToOne: false;
            referencedRelation: "empresas";
            referencedColumns: ["id"];
          },
        ];
      };
      debitos: {
        Row: {
          id: string;
          empresa_id: string;
          consulta_origem_id: string | null;
          codigo_receita: string | null;
          descricao: string;
          periodo_apuracao: string | null;
          data_vencimento: string | null;
          valor_original: number | null;
          multa: number | null;
          juros: number | null;
          saldo_devedor: number | null;
          situacao: Database["public"]["Enums"]["debito_situacao"];
          secao_origem: string;
          confianca: Database["public"]["Enums"]["confianca_parse"];
          hash_identidade: string;
          linha_bruta: string | null;
          raw: Json;
          primeira_deteccao_em: string;
          ultima_vista_em: string;
          resolvido_em: string | null;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          empresa_id: string;
          consulta_origem_id?: string | null;
          codigo_receita?: string | null;
          descricao: string;
          periodo_apuracao?: string | null;
          data_vencimento?: string | null;
          valor_original?: number | null;
          multa?: number | null;
          juros?: number | null;
          saldo_devedor?: number | null;
          situacao?: Database["public"]["Enums"]["debito_situacao"];
          secao_origem: string;
          confianca?: Database["public"]["Enums"]["confianca_parse"];
          hash_identidade: string;
          linha_bruta?: string | null;
          raw?: Json;
          primeira_deteccao_em?: string;
          ultima_vista_em?: string;
          resolvido_em?: string | null;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          empresa_id?: string;
          consulta_origem_id?: string | null;
          codigo_receita?: string | null;
          descricao?: string;
          periodo_apuracao?: string | null;
          data_vencimento?: string | null;
          valor_original?: number | null;
          multa?: number | null;
          juros?: number | null;
          saldo_devedor?: number | null;
          situacao?: Database["public"]["Enums"]["debito_situacao"];
          secao_origem?: string;
          confianca?: Database["public"]["Enums"]["confianca_parse"];
          hash_identidade?: string;
          linha_bruta?: string | null;
          raw?: Json;
          primeira_deteccao_em?: string;
          ultima_vista_em?: string;
          resolvido_em?: string | null;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [
          {
            foreignKeyName: "debitos_consulta_origem_id_fkey";
            columns: ["consulta_origem_id"];
            isOneToOne: false;
            referencedRelation: "sitfis_consultas";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "debitos_empresa_id_fkey";
            columns: ["empresa_id"];
            isOneToOne: false;
            referencedRelation: "empresas";
            referencedColumns: ["id"];
          },
        ];
      };
      empresas: {
        Row: {
          id: string;
          cnpj: string;
          razao_social: string;
          nome_fantasia: string | null;
          whatsapp: string;
          email: string | null;
          procurador_id: string | null;
          status: Database["public"]["Enums"]["empresa_status"];
          avisos_ativos: boolean;
          procuracao_ecac_ok: boolean;
          consentimento_whatsapp_em: string | null;
          observacao: string | null;
          created_at: string;
          updated_at: string;
          opt_out_em: string | null;
          opt_out_origem: string | null;
        };
        Insert: {
          id?: string;
          cnpj: string;
          razao_social: string;
          nome_fantasia?: string | null;
          whatsapp: string;
          email?: string | null;
          procurador_id?: string | null;
          status?: Database["public"]["Enums"]["empresa_status"];
          avisos_ativos?: boolean;
          procuracao_ecac_ok?: boolean;
          consentimento_whatsapp_em?: string | null;
          observacao?: string | null;
          created_at?: string;
          updated_at?: string;
          opt_out_em?: string | null;
          opt_out_origem?: string | null;
        };
        Update: {
          id?: string;
          cnpj?: string;
          razao_social?: string;
          nome_fantasia?: string | null;
          whatsapp?: string;
          email?: string | null;
          procurador_id?: string | null;
          status?: Database["public"]["Enums"]["empresa_status"];
          avisos_ativos?: boolean;
          procuracao_ecac_ok?: boolean;
          consentimento_whatsapp_em?: string | null;
          observacao?: string | null;
          created_at?: string;
          updated_at?: string;
          opt_out_em?: string | null;
          opt_out_origem?: string | null;
        };
        Relationships: [
          {
            foreignKeyName: "empresas_procurador_id_fkey";
            columns: ["procurador_id"];
            isOneToOne: false;
            referencedRelation: "procuradores";
            referencedColumns: ["id"];
          },
        ];
      };
      interacoes: {
        Row: {
          id: string;
          conversa_id: string;
          empresa_id: string | null;
          aviso_id: string | null;
          mensagem_id: string | null;
          opcao: Database["public"]["Enums"]["interacao_opcao"];
          data_recalculo: string | null;
          created_at: string;
        };
        Insert: {
          id?: string;
          conversa_id: string;
          empresa_id?: string | null;
          aviso_id?: string | null;
          mensagem_id?: string | null;
          opcao: Database["public"]["Enums"]["interacao_opcao"];
          data_recalculo?: string | null;
          created_at?: string;
        };
        Update: {
          id?: string;
          conversa_id?: string;
          empresa_id?: string | null;
          aviso_id?: string | null;
          mensagem_id?: string | null;
          opcao?: Database["public"]["Enums"]["interacao_opcao"];
          data_recalculo?: string | null;
          created_at?: string;
        };
        Relationships: [
          {
            foreignKeyName: "interacoes_aviso_id_fkey";
            columns: ["aviso_id"];
            isOneToOne: false;
            referencedRelation: "avisos";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "interacoes_conversa_id_fkey";
            columns: ["conversa_id"];
            isOneToOne: false;
            referencedRelation: "conversas";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "interacoes_empresa_id_fkey";
            columns: ["empresa_id"];
            isOneToOne: false;
            referencedRelation: "empresas";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "interacoes_mensagem_id_fkey";
            columns: ["mensagem_id"];
            isOneToOne: false;
            referencedRelation: "mensagens";
            referencedColumns: ["id"];
          },
        ];
      };
      job_queue: {
        Row: {
          id: number;
          tipo: string;
          payload: Json;
          status: Database["public"]["Enums"]["job_status"];
          prioridade: number;
          tentativas: number;
          max_tentativas: number;
          agendado_para: string;
          iniciado_em: string | null;
          concluido_em: string | null;
          erro: string | null;
          chave_dedupe: string | null;
          created_at: string;
        };
        Insert: {
          id?: number;
          tipo: string;
          payload?: Json;
          status?: Database["public"]["Enums"]["job_status"];
          prioridade?: number;
          tentativas?: number;
          max_tentativas?: number;
          agendado_para?: string;
          iniciado_em?: string | null;
          concluido_em?: string | null;
          erro?: string | null;
          chave_dedupe?: string | null;
          created_at?: string;
        };
        Update: {
          id?: number;
          tipo?: string;
          payload?: Json;
          status?: Database["public"]["Enums"]["job_status"];
          prioridade?: number;
          tentativas?: number;
          max_tentativas?: number;
          agendado_para?: string;
          iniciado_em?: string | null;
          concluido_em?: string | null;
          erro?: string | null;
          chave_dedupe?: string | null;
          created_at?: string;
        };
        Relationships: [
        ];
      };
      mensagens: {
        Row: {
          id: string;
          empresa_id: string | null;
          direcao: Database["public"]["Enums"]["mensagem_direcao"];
          whatsapp: string;
          corpo: string;
          template_id: string | null;
          evolution_message_id: string | null;
          status: Database["public"]["Enums"]["mensagem_status"];
          erro: string | null;
          anexo_storage_path: string | null;
          payload: Json;
          enviado_em: string | null;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          empresa_id?: string | null;
          direcao: Database["public"]["Enums"]["mensagem_direcao"];
          whatsapp: string;
          corpo?: string;
          template_id?: string | null;
          evolution_message_id?: string | null;
          status?: Database["public"]["Enums"]["mensagem_status"];
          erro?: string | null;
          anexo_storage_path?: string | null;
          payload?: Json;
          enviado_em?: string | null;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          empresa_id?: string | null;
          direcao?: Database["public"]["Enums"]["mensagem_direcao"];
          whatsapp?: string;
          corpo?: string;
          template_id?: string | null;
          evolution_message_id?: string | null;
          status?: Database["public"]["Enums"]["mensagem_status"];
          erro?: string | null;
          anexo_storage_path?: string | null;
          payload?: Json;
          enviado_em?: string | null;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [
          {
            foreignKeyName: "mensagens_empresa_id_fkey";
            columns: ["empresa_id"];
            isOneToOne: false;
            referencedRelation: "empresas";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "mensagens_template_id_fkey";
            columns: ["template_id"];
            isOneToOne: false;
            referencedRelation: "templates";
            referencedColumns: ["id"];
          },
        ];
      };
      procurador_certificado_segredos: {
        Row: {
          certificado_id: string;
          senha_cipher: string;
          pfx_sha256: string;
          created_at: string;
        };
        Insert: {
          certificado_id: string;
          senha_cipher: string;
          pfx_sha256: string;
          created_at?: string;
        };
        Update: {
          certificado_id?: string;
          senha_cipher?: string;
          pfx_sha256?: string;
          created_at?: string;
        };
        Relationships: [
          {
            foreignKeyName: "procurador_certificado_segredos_certificado_id_fkey";
            columns: ["certificado_id"];
            isOneToOne: true;
            referencedRelation: "procurador_certificados";
            referencedColumns: ["id"];
          },
        ];
      };
      procurador_certificados: {
        Row: {
          id: string;
          procurador_id: string;
          storage_path: string;
          subject_cn: string;
          issuer_cn: string;
          fingerprint_sha256: string;
          documento_subject: string | null;
          not_before: string;
          not_after: string;
          ativo: boolean;
          enviado_por: string | null;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          procurador_id: string;
          storage_path: string;
          subject_cn: string;
          issuer_cn: string;
          fingerprint_sha256: string;
          documento_subject?: string | null;
          not_before: string;
          not_after: string;
          ativo?: boolean;
          enviado_por?: string | null;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          procurador_id?: string;
          storage_path?: string;
          subject_cn?: string;
          issuer_cn?: string;
          fingerprint_sha256?: string;
          documento_subject?: string | null;
          not_before?: string;
          not_after?: string;
          ativo?: boolean;
          enviado_por?: string | null;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [
          {
            foreignKeyName: "procurador_certificados_enviado_por_fkey";
            columns: ["enviado_por"];
            isOneToOne: false;
            referencedRelation: "profiles";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "procurador_certificados_procurador_id_fkey";
            columns: ["procurador_id"];
            isOneToOne: false;
            referencedRelation: "procuradores";
            referencedColumns: ["id"];
          },
        ];
      };
      procuradores: {
        Row: {
          id: string;
          nome: string;
          cpf_cnpj: string;
          tipo: Database["public"]["Enums"]["procurador_tipo"];
          status: Database["public"]["Enums"]["procurador_status"];
          observacao: string | null;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          nome: string;
          cpf_cnpj: string;
          tipo: Database["public"]["Enums"]["procurador_tipo"];
          status?: Database["public"]["Enums"]["procurador_status"];
          observacao?: string | null;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          nome?: string;
          cpf_cnpj?: string;
          tipo?: Database["public"]["Enums"]["procurador_tipo"];
          status?: Database["public"]["Enums"]["procurador_status"];
          observacao?: string | null;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [
        ];
      };
      profiles: {
        Row: {
          id: string;
          nome: string;
          email: string;
          papel: Database["public"]["Enums"]["perfil_papel"];
          ativo: boolean;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id: string;
          nome?: string;
          email: string;
          papel?: Database["public"]["Enums"]["perfil_papel"];
          ativo?: boolean;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          nome?: string;
          email?: string;
          papel?: Database["public"]["Enums"]["perfil_papel"];
          ativo?: boolean;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [
          {
            foreignKeyName: "profiles_id_fkey";
            columns: ["id"];
            isOneToOne: true;
            referencedRelation: "users";
            referencedColumns: ["id"];
          },
        ];
      };
      sitfis_consultas: {
        Row: {
          id: string;
          empresa_id: string;
          procurador_id: string | null;
          protocolo: string | null;
          status: Database["public"]["Enums"]["sitfis_status"];
          tempo_espera_ms: number | null;
          tentativas: number;
          pdf_storage_path: string | null;
          pdf_sha256: string | null;
          parse_status: Database["public"]["Enums"]["parse_status"];
          parse_resumo: Json | null;
          erro: string | null;
          iniciado_em: string;
          concluido_em: string | null;
        };
        Insert: {
          id?: string;
          empresa_id: string;
          procurador_id?: string | null;
          protocolo?: string | null;
          status?: Database["public"]["Enums"]["sitfis_status"];
          tempo_espera_ms?: number | null;
          tentativas?: number;
          pdf_storage_path?: string | null;
          pdf_sha256?: string | null;
          parse_status?: Database["public"]["Enums"]["parse_status"];
          parse_resumo?: Json | null;
          erro?: string | null;
          iniciado_em?: string;
          concluido_em?: string | null;
        };
        Update: {
          id?: string;
          empresa_id?: string;
          procurador_id?: string | null;
          protocolo?: string | null;
          status?: Database["public"]["Enums"]["sitfis_status"];
          tempo_espera_ms?: number | null;
          tentativas?: number;
          pdf_storage_path?: string | null;
          pdf_sha256?: string | null;
          parse_status?: Database["public"]["Enums"]["parse_status"];
          parse_resumo?: Json | null;
          erro?: string | null;
          iniciado_em?: string;
          concluido_em?: string | null;
        };
        Relationships: [
          {
            foreignKeyName: "sitfis_consultas_empresa_id_fkey";
            columns: ["empresa_id"];
            isOneToOne: false;
            referencedRelation: "empresas";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "sitfis_consultas_procurador_id_fkey";
            columns: ["procurador_id"];
            isOneToOne: false;
            referencedRelation: "procuradores";
            referencedColumns: ["id"];
          },
        ];
      };
      tarefas: {
        Row: {
          id: string;
          tipo: Database["public"]["Enums"]["tarefa_tipo"];
          status: Database["public"]["Enums"]["tarefa_status"];
          titulo: string;
          detalhe: string | null;
          empresa_id: string | null;
          debito_id: string | null;
          procurador_id: string | null;
          conversa_id: string | null;
          contexto: Json;
          chave_dedupe: string | null;
          responsavel: string | null;
          resolvido_em: string | null;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          tipo: Database["public"]["Enums"]["tarefa_tipo"];
          status?: Database["public"]["Enums"]["tarefa_status"];
          titulo: string;
          detalhe?: string | null;
          empresa_id?: string | null;
          debito_id?: string | null;
          procurador_id?: string | null;
          conversa_id?: string | null;
          contexto?: Json;
          chave_dedupe?: string | null;
          responsavel?: string | null;
          resolvido_em?: string | null;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          tipo?: Database["public"]["Enums"]["tarefa_tipo"];
          status?: Database["public"]["Enums"]["tarefa_status"];
          titulo?: string;
          detalhe?: string | null;
          empresa_id?: string | null;
          debito_id?: string | null;
          procurador_id?: string | null;
          conversa_id?: string | null;
          contexto?: Json;
          chave_dedupe?: string | null;
          responsavel?: string | null;
          resolvido_em?: string | null;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [
          {
            foreignKeyName: "tarefas_conversa_id_fkey";
            columns: ["conversa_id"];
            isOneToOne: false;
            referencedRelation: "conversas";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "tarefas_debito_id_fkey";
            columns: ["debito_id"];
            isOneToOne: false;
            referencedRelation: "debitos";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "tarefas_empresa_id_fkey";
            columns: ["empresa_id"];
            isOneToOne: false;
            referencedRelation: "empresas";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "tarefas_procurador_id_fkey";
            columns: ["procurador_id"];
            isOneToOne: false;
            referencedRelation: "procuradores";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "tarefas_responsavel_fkey";
            columns: ["responsavel"];
            isOneToOne: false;
            referencedRelation: "profiles";
            referencedColumns: ["id"];
          },
        ];
      };
      templates: {
        Row: {
          id: string;
          chave: string;
          titulo: string;
          corpo: string;
          descricao: string | null;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          chave: string;
          titulo: string;
          corpo: string;
          descricao?: string | null;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          chave?: string;
          titulo?: string;
          corpo?: string;
          descricao?: string | null;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [
        ];
      };
    };
    Views: {
      avisos_detalhe: {
        Row: {
          id: string | null;
          empresa_id: string | null;
          cnpj: string | null;
          razao_social: string | null;
          whatsapp: string | null;
          marco: Database["public"]["Enums"]["marco"] | null;
          agendado_para: string | null;
          status: Database["public"]["Enums"]["aviso_status"] | null;
          tentativas: number | null;
          erro: string | null;
          enviado_em: string | null;
          created_at: string | null;
          mensagem_id: string | null;
          status_mensagem: Database["public"]["Enums"]["mensagem_status"] | null;
          evolution_message_id: string | null;
          qtd_debitos: number | null;
          total: number | null;
          marco_agendado_para: string | null;
        };
        Relationships: [];
      };
      configuracoes_publicas: {
        Row: {
          chave: string | null;
          valor: Json | null;
          descricao: string | null;
          updated_at: string | null;
          updated_by: string | null;
        };
        Relationships: [];
      };
      debitos_abertos: {
        Row: {
          id: string | null;
          empresa_id: string | null;
          cnpj: string | null;
          razao_social: string | null;
          whatsapp: string | null;
          avisos_ativos: boolean | null;
          codigo_receita: string | null;
          descricao: string | null;
          periodo_apuracao: string | null;
          data_vencimento: string | null;
          valor_original: number | null;
          multa: number | null;
          juros: number | null;
          saldo_devedor: number | null;
          situacao: Database["public"]["Enums"]["debito_situacao"] | null;
          confianca: Database["public"]["Enums"]["confianca_parse"] | null;
          secao_origem: string | null;
          motivo_baixa_confianca: string | null;
          primeira_deteccao_em: string | null;
          ultima_vista_em: string | null;
          dias_atraso: number | null;
          faixa_atraso: Database["public"]["Enums"]["faixa_atraso"] | null;
          cobravel: boolean | null;
        };
        Relationships: [];
      };
      empresas_resumo: {
        Row: {
          empresa_id: string | null;
          cnpj: string | null;
          razao_social: string | null;
          nome_fantasia: string | null;
          whatsapp: string | null;
          status: Database["public"]["Enums"]["empresa_status"] | null;
          avisos_ativos: boolean | null;
          procuracao_ecac_ok: boolean | null;
          consentimento_whatsapp_em: string | null;
          procurador_id: string | null;
          procurador_nome: string | null;
          qtd_debitos: number | null;
          qtd_cobraveis: number | null;
          qtd_conferir: number | null;
          total_aberto: number | null;
          total_cobravel: number | null;
          maior_atraso_dias: number | null;
          ultima_sincronizacao_em: string | null;
          ultima_sincronizacao_status: Database["public"]["Enums"]["sitfis_status"] | null;
          ultima_sincronizacao_parse: Database["public"]["Enums"]["parse_status"] | null;
        };
        Relationships: [];
      };
      resumo_faixas: {
        Row: {
          faixa_atraso: Database["public"]["Enums"]["faixa_atraso"] | null;
          qtd_debitos: number | null;
          qtd_empresas: number | null;
          total: number | null;
          qtd_cobraveis: number | null;
          total_cobravel: number | null;
        };
        Relationships: [];
      };
    };
    Functions: {
      [_ in never]: never;
    };
    Enums: {
      aviso_status: "pendente" | "enviado" | "falhou" | "cancelado";
      confianca_parse: "alta" | "baixa";
      conversa_estado: "idle" | "aguardando_opcao" | "aguardando_data_recalculo" | "humano";
      darf_status: "aguardando_aprovacao" | "gerado" | "enviado" | "falhou";
      debito_situacao: "devedor" | "exigibilidade_suspensa" | "em_parcelamento" | "divida_ativa" | "quitado";
      empresa_status: "ativo" | "inativo";
      faixa_atraso: "a_vencer" | "d0_4" | "d5_14" | "d15_29" | "d30_59" | "d60_89" | "d90_mais" | "sem_data";
      interacao_opcao: "ciente_recalculo" | "ciente_sem_recalculo" | "falar_humano" | "opt_out";
      job_status: "pendente" | "processando" | "concluido" | "falhou";
      marco: "d5" | "d15" | "d30" | "d60" | "d90";
      marco_status: "pendente" | "enviado" | "falhou" | "suprimido";
      mensagem_direcao: "entrada" | "saida";
      mensagem_status: "fila" | "enviada" | "entregue" | "lida" | "falhou";
      parse_status: "pendente" | "ok" | "parcial" | "falhou";
      perfil_papel: "admin" | "operador";
      procurador_status: "ativo" | "inativo";
      procurador_tipo: "ecpf" | "ecnpj";
      sitfis_status: "solicitado" | "aguardando" | "concluido" | "erro" | "expirado";
      tarefa_status: "aberta" | "em_andamento" | "resolvida" | "cancelada";
      tarefa_tipo: "recalculo" | "falar_humano" | "erro_certificado" | "certificado_vencendo" | "erro_sitfis" | "parse_baixa_confianca" | "erro_darf" | "aprovacao_darf" | "numero_desconhecido" | "falha_envio";
    };
    CompositeTypes: {
      [_ in never]: never;
    };
  };
};

