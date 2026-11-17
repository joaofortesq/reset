package br.manutencao.reset

import android.hardware.usb.UsbDevice
import android.os.Bundle
import android.util.Log
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.File
import java.net.ConnectException
import java.net.SocketTimeoutException
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

private val AZUL = Color(0xFF004966)
private val AZUL_CLARO = Color(0xFF0A6A91)
private val AZUL_SUAVE = Color(0xFFDCE9EF)
private val CINZA = Color(0xFF666363)
private val CINZA_ESCURO = Color(0xFF3F3D3D)
private val CINZA_CLARO = Color(0xFFECEDEE)
private val CINZA_LINHA = Color(0xFFD5D6D7)
private val VERDE = Color(0xFF2E8B57)
private val LARANJA = Color(0xFFC77700)
private val VERMELHO = Color(0xFFB3261E)
private val COR_CONF = mapOf("A" to VERDE, "B" to AZUL_CLARO, "C" to LARANJA, "D" to VERMELHO)

/** Impressora encontrada: pela USB ou pela rede. */
sealed class Alvo(val deviceId: Map<String, String>) {
    class Usb(val device: UsbDevice, did: Map<String, String>) : Alvo(did)
    class Rede(val ip: String, did: Map<String, String>) : Alvo(did)

    val modelo get() = (deviceId["MDL"] ?: "?").replace(" Series", "").trim()
    val via get() = when (this) { is Usb -> "USB"; is Rede -> "Wi‑Fi $ip" }
}

data class LinhaLog(val texto: String, val cor: Color)
data class Aviso(val titulo: String, val texto: String)

class MainActivity : ComponentActivity() {

    private val impressoras = mutableStateListOf<Alvo>()
    private var selecionada by mutableStateOf<Alvo?>(null)
    private var perfil by mutableStateOf<Perfil?>(null)
    private var contadores by mutableStateOf<Map<String, Leitura>?>(null)
    private val registro = mutableStateListOf<LinhaLog>()
    private var ocupado by mutableStateOf(false)
    private var status by mutableStateOf("Pronto")
    private var ipDigitado by mutableStateOf("")
    private var aviso by mutableStateOf<Aviso?>(null)
    private var confirmar by mutableStateOf<String?>(null)  // "reset" ou "intensa"
    private var etiquetaLeitura by mutableStateOf<String?>(null)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme(colorScheme = lightColorScheme(primary = AZUL, secondary = CINZA)) { Tela() }
        }
        detectar()
    }

    // ---------------- utilidades ----------------
    private fun escrever(t: String, cor: Color = CINZA_ESCURO) { registro.add(LinhaLog(t, cor)) }

    private fun amigavel(e: Throwable, acao: String): Aviso = when (e) {
        is GravacaoRecusada -> if (e.gravados == 0)
            Aviso("Reset não realizado", "A impressora não aceitou a gravação. Nenhum contador foi alterado.\n\nEste modelo pode ter o firmware bloqueado para reset.")
        else Aviso("Reset incompleto", "O reset parou no meio (${e.gravados} itens gravados). Os valores originais estão salvos nos backups do app.\n\nDesligue e ligue a impressora e tente de novo.")
        is LeituraRecusada -> Aviso("Leitura não liberada", "A impressora não liberou a leitura dos contadores.\n\nO firmware pode estar bloqueado. A limpeza e a verificação de jatos continuam disponíveis.")
        is D4Erro -> if (e.etapa == "entrar")
            Aviso("Impressora não respondeu", "A impressora não entrou no modo de manutenção.\n\nVerifique se ela está ligada, sem luz de erro piscando e sem impressão em andamento.")
        else Aviso("Comunicação interrompida", "A impressora parou de responder.\n\nVerifique o cabo e se a impressora está ligada, depois toque em Detectar.")
        is SnmpErro -> Aviso("Sem resposta pela rede", "A impressora não respondeu pela rede.\n\nConfira se o celular e a impressora estão na mesma rede Wi‑Fi. Redes de visitantes costumam bloquear isso.")
        is ConnectException, is SocketTimeoutException -> Aviso("Sem conexão com a impressora", "Não foi possível enviar o comando pela rede.\n\nVerifique se a impressora está ligada e na mesma rede.")
        is UsbErro -> Aviso("Problema na USB", "Não foi possível falar com a impressora pelo cabo.\n\nConfira o cabo/adaptador OTG e se o app tem permissão de USB.")
        else -> Aviso("Erro inesperado", "Não foi possível concluir: ${acao.lowercase()}.")
    }

    private fun tarefa(nome: String, bloco: suspend () -> Unit) {
        if (ocupado) return
        ocupado = true
        status = "$nome…"
        lifecycleScope.launch {
            try {
                bloco()
                status = "Pronto"
            } catch (e: Throwable) {
                Log.e("Manutencao", "Falha em $nome", e)
                val a = amigavel(e, nome)
                escrever("✖ ${a.titulo} — ${a.texto.lineSequence().first()}", VERMELHO)
                status = a.titulo
                aviso = a
            } finally {
                ocupado = false
            }
        }
    }

    // ---------------- detecção ----------------
    private fun detectar() = tarefa("Detectando") {
        val lista = mutableListOf<Alvo>()
        for (d in UsbEpson.listar(this)) {
            if (!UsbEpson.pedirPermissao(this, d)) {
                escrever("Permissão de USB negada para a impressora.", VERMELHO); continue
            }
            val did = withContext(Dispatchers.IO) { UsbEpson.abrir(this@MainActivity, d).use { it.deviceId() } }
            val m = parseDeviceId(did)
            if (m["MDL"] != null) lista += Alvo.Usb(d, m)
        }
        status = "Procurando na rede…"
        val rede = withContext(Dispatchers.IO) { try { Snmp.descobrir(this@MainActivity) } catch (e: Exception) { emptyList() } }
        for ((ip, txt) in rede) {
            val m = parseDeviceId(txt)
            if (m["MDL"] != null) lista += Alvo.Rede(ip, m)
        }
        impressoras.clear(); impressoras.addAll(lista)
        if (lista.isEmpty()) {
            selecionar(null)
            escrever("Nenhuma Epson encontrada na USB nem na rede. Se souber o IP, digite e toque em Conectar.", VERMELHO)
        } else selecionar(lista.first())
    }

    private fun conectarIp() {
        val ip = ipDigitado.trim()
        if (!Regex("^\\d{1,3}(\\.\\d{1,3}){3}$").matches(ip)) {
            aviso = Aviso("IP inválido", "Digite o IP da impressora, por exemplo 192.168.0.50.\n\nO IP aparece na folha de status de rede da impressora.")
            return
        }
        tarefa("Conectando em $ip") {
            val txt = withContext(Dispatchers.IO) { try { Snmp.deviceId(ip) } catch (e: SnmpSemObjeto) { "" } }
            val m = parseDeviceId(txt)
            if (m["MDL"] == null || !(m["MFG"] ?: "EPSON").uppercase().contains("EPSON")) {
                aviso = Aviso("Não é uma Epson", "O aparelho no IP $ip respondeu, mas não é uma impressora Epson.")
                return@tarefa
            }
            impressoras.removeAll { it is Alvo.Rede && it.ip == ip }
            val novo = Alvo.Rede(ip, m)
            impressoras.add(novo)
            selecionar(novo)
        }
    }

    private fun selecionar(a: Alvo?) {
        selecionada = a
        contadores = null
        etiquetaLeitura = null
        perfil = a?.let { Modelos.achar(this, it.deviceId["MDL"] ?: "") }
        if (a != null) {
            escrever("Impressora encontrada: EPSON ${a.modelo} (${a.via})", AZUL)
            if (perfil != null && perfil?.reset == null)
                escrever("Para este modelo o reset ainda não está disponível. Leitura, limpeza e jatos funcionam.", AZUL)
        }
    }

    // ---------------- sessão com a impressora ----------------
    private suspend fun <T> sessao(bloco: (Epson) -> T): T = withContext(Dispatchers.IO) {
        val a = selecionada ?: throw UsbErro("Nenhuma impressora selecionada")
        val p = perfil ?: throw LeituraRecusada("Modelo sem perfil")
        when (a) {
            is Alvo.Rede -> bloco(Epson(RedeCanal(a.ip), p))
            is Alvo.Usb -> UsbEpson.abrir(this@MainActivity, a.device).use { porta ->
                val link = D4Link(porta)
                link.entrar()
                try {
                    link.abrir(D4Link.CTRL)
                    bloco(Epson(link, p))
                } finally {
                    link.fechar()
                }
            }
        }
    }

    private fun ler() = tarefa("Lendo contadores") {
        val c = sessao { it.contadores() }
        contadores = c
        if (perfil?.confianca == "D") etiquetaLeitura = "Leitura liberada (firmware não bloqueado)"
        c.forEach { (n, l) -> escrever("${NOMES_CONTADOR[n] ?: n}: ${l.percentual?.let { "%.2f%%".format(it) } ?: l.valor}") }
    }

    private fun salvarBackup(dados: Map<Int, Int>): File {
        val pasta = File(getExternalFilesDir(null), "backups").apply { mkdirs() }
        val nome = "backup_${perfil?.chave}_${SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(Date())}.json"
        val j = JSONObject()
        j.put("modelo", selecionada?.deviceId?.get("MDL"))
        j.put("via", selecionada?.via)
        j.put("eeprom", JSONObject(dados.mapKeys { it.key.toString() }))
        return File(pasta, nome).apply { writeText(j.toString(1)) }
    }

    private fun reset() = tarefa("Resetando") {
        val depois = sessao { ep ->
            val bkp = ep.backup()
            salvarBackup(bkp)
            ep.resetar { i, n -> lifecycleScope.launch { status = "Gravando $i/$n…" } }
            ep.contadores()
        }
        escrever("Backup dos contadores salvo no celular.", AZUL)
        contadores = depois
        escrever("✔ Reset concluído. Desligue e ligue a impressora.", VERDE)
        aviso = Aviso("Reset concluído", "Contadores zerados.\nDesligue e ligue a impressora.")
    }

    private fun manutencao(titulo: String, dados: ByteArray) = tarefa(titulo) {
        val a = selecionada ?: return@tarefa
        withContext(Dispatchers.IO) {
            when (a) {
                is Alvo.Rede -> Snmp.enviarRaw9100(a.ip, dados)
                is Alvo.Usb -> UsbEpson.abrir(this@MainActivity, a.device).use { it.write(dados) }
            }
        }
        val fim = if (titulo.startsWith("Verificação")) "Aguarde a folha de teste sair."
        else "Aguarde a impressora terminar (1 a 2 minutos)."
        escrever("✔ $titulo enviada. $fim", VERDE)
    }

    // ---------------- interface ----------------
    @Composable
    private fun Tela() {
        Column(Modifier.fillMaxSize().background(CINZA_CLARO)) {
            Column(Modifier.fillMaxWidth().background(AZUL).statusBarsPadding().padding(horizontal = 18.dp, vertical = 14.dp)) {
                Text("Manutenção Epson", color = Color.White, fontSize = 22.sp, fontWeight = FontWeight.Bold)
                Text("Reset de almofadas · Limpeza · Verificação de jatos", color = Color(0xFF9FC3D3), fontSize = 12.sp)
            }
            Box(Modifier.fillMaxWidth().height(3.dp).background(Color(0xFF7E7B7B)))
            Column(
                Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(14.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                CartaoImpressora()
                CartaoContadores()
                CartaoAcoes()
                CartaoRegistro()
            }
            Row(Modifier.fillMaxWidth().background(CINZA).navigationBarsPadding().padding(horizontal = 12.dp, vertical = 6.dp)) {
                Text(status, color = Color.White, fontSize = 12.sp, modifier = Modifier.weight(1f))
                Text("${Modelos.carregar(this@MainActivity).size} perfis", color = Color(0xFFD9D9D9), fontSize = 11.sp)
            }
        }
        aviso?.let { a ->
            AlertDialog(onDismissRequest = { aviso = null }, title = { Text(a.titulo) }, text = { Text(a.texto) },
                confirmButton = { TextButton(onClick = { aviso = null }) { Text("OK") } })
        }
        confirmar?.let { c ->
            val (titulo, texto) = if (c == "reset") {
                val conf = perfil?.confianca
                val extra = if (conf == "C" || conf == "D") "\n\nAtenção: perfil de confiança $conf (${CONFIANCA[conf]})." else ""
                "Resetar almofadas" to "Zerar os contadores das almofadas da EPSON ${selecionada?.modelo}?\n\nUm backup dos valores atuais será salvo antes. Troque ou limpe as almofadas físicas.$extra"
            } else "Limpeza intensa" to "A limpeza intensa gasta bastante tinta.\nUse só se a limpeza normal não resolveu.\n\nContinuar?"
            AlertDialog(onDismissRequest = { confirmar = null }, title = { Text(titulo) }, text = { Text(texto) },
                confirmButton = {
                    TextButton(onClick = {
                        confirmar = null
                        if (c == "reset") reset() else manutencao("Limpeza intensa", Comandos.limpeza(true))
                    }) { Text("Sim") }
                },
                dismissButton = { TextButton(onClick = { confirmar = null }) { Text("Não") } })
        }
    }

    @Composable
    private fun Cartao(titulo: String, conteudo: @Composable () -> Unit) {
        Column(
            Modifier.fillMaxWidth().background(Color.White, RoundedCornerShape(6.dp))
                .border(1.dp, CINZA_LINHA, RoundedCornerShape(6.dp)).padding(16.dp),
        ) {
            Text(titulo.uppercase(), color = CINZA, fontSize = 11.sp, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(8.dp))
            conteudo()
        }
    }

    @Composable
    private fun CartaoImpressora() = Cartao("Impressora") {
        val a = selecionada
        Text(if (a != null) "EPSON ${a.modelo}" else if (ocupado) "Procurando…" else "Nenhuma Epson encontrada",
            color = AZUL, fontSize = 20.sp, fontWeight = FontWeight.Bold)
        val p = perfil
        if (a != null) {
            val familia = when {
                p == null -> "Modelo sem reset cadastrado"
                p.chave.uppercase() == a.modelo.uppercase() -> "Perfil próprio"
                else -> "Mesma família da ${p.chave}"
            }
            Text("$familia\nConectada ${if (a is Alvo.Rede) "pela rede (${a.ip})" else "pela USB"}", color = CINZA, fontSize = 13.sp)
            Spacer(Modifier.height(6.dp))
            val (txt, cor) = when {
                etiquetaLeitura != null -> etiquetaLeitura!! to LARANJA
                p != null -> (CONFIANCA[p.confianca] ?: p.confianca) to (COR_CONF[p.confianca] ?: CINZA)
                else -> "Só limpeza e teste de jatos" to CINZA
            }
            Text(txt, color = Color.White, fontSize = 11.sp, fontWeight = FontWeight.Bold,
                modifier = Modifier.background(cor).padding(horizontal = 8.dp, vertical = 2.dp))
        } else {
            Text("Conecte pelo cabo USB (OTG) ou deixe a impressora na mesma rede Wi‑Fi do celular.", color = CINZA, fontSize = 13.sp)
        }
        if (impressoras.size > 1) {
            Spacer(Modifier.height(8.dp))
            impressoras.forEach { item ->
                val sel = item === selecionada
                Text("${if (sel) "●" else "○"}  ${item.modelo}  ·  ${item.via}",
                    color = if (sel) AZUL else CINZA_ESCURO, fontSize = 14.sp,
                    modifier = Modifier.fillMaxWidth().clickable(enabled = !ocupado) { selecionar(item) }.padding(vertical = 6.dp))
            }
        }
        Spacer(Modifier.height(10.dp))
        Botao("⟳  Detectar", CINZA, Color.White, !ocupado, Modifier.fillMaxWidth()) { detectar() }
        Spacer(Modifier.height(8.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            OutlinedTextField(value = ipDigitado, onValueChange = { ipDigitado = it }, singleLine = true,
                label = { Text("IP da impressora") }, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
                modifier = Modifier.weight(1f))
            Spacer(Modifier.width(8.dp))
            Botao("Conectar", AZUL, Color.White, !ocupado) { conectarIp() }
        }
    }

    @Composable
    private fun CartaoContadores() = Cartao("Contadores das almofadas") {
        val c = contadores
        if (c == null) Text("Toque em “Ler contadores”.", color = CINZA, fontSize = 13.sp)
        else c.forEach { (nome, l) ->
            Text(NOMES_CONTADOR[nome] ?: nome, color = CINZA_ESCURO, fontSize = 13.sp)
            Row(verticalAlignment = Alignment.CenterVertically) {
                val pct = l.percentual ?: 0.0
                LinearProgressIndicator(progress = { (pct / 100.0).coerceIn(0.0, 1.0).toFloat() },
                    color = if (pct >= 90) VERMELHO else AZUL, trackColor = CINZA_CLARO,
                    modifier = Modifier.weight(1f).height(12.dp))
                Text(l.percentual?.let { "%.1f%%".format(it) } ?: "valor ${l.valor}", color = AZUL,
                    fontWeight = FontWeight.Bold, fontSize = 14.sp, modifier = Modifier.width(84.dp).padding(start = 8.dp))
            }
            Spacer(Modifier.height(8.dp))
        }
    }

    @Composable
    private fun CartaoAcoes() = Cartao("Ações") {
        val tem = selecionada != null
        val p = perfil
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Botao("Ler contadores", CINZA, Color.White, !ocupado && p != null, Modifier.weight(1f)) { ler() }
                Botao("Resetar almofadas", AZUL, Color.White, !ocupado && p?.reset != null, Modifier.weight(1f)) { confirmar = "reset" }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Botao("Verificar jatos", AZUL_SUAVE, AZUL, !ocupado && tem, Modifier.weight(1f)) {
                    manutencao("Verificação de jatos", Comandos.testeJatos())
                }
                Botao("Limpeza de cabeçote", AZUL_SUAVE, AZUL, !ocupado && tem, Modifier.weight(1f)) {
                    manutencao("Limpeza de cabeçote", Comandos.limpeza(false))
                }
            }
            Botao("Limpeza intensa", CINZA_ESCURO, Color.White, !ocupado && tem, Modifier.fillMaxWidth()) { confirmar = "intensa" }
        }
    }

    @Composable
    private fun CartaoRegistro() = Cartao("Registro") {
        if (registro.isEmpty()) Text("—", color = CINZA)
        registro.takeLast(30).forEach { Text(it.texto, color = it.cor, fontSize = 12.sp, fontFamily = FontFamily.Monospace) }
    }

    @Composable
    private fun Botao(texto: String, fundo: Color, frente: Color, ativo: Boolean, modifier: Modifier = Modifier, aoClicar: () -> Unit) {
        Button(onClick = aoClicar, enabled = ativo, shape = RoundedCornerShape(4.dp), modifier = modifier.height(48.dp),
            colors = ButtonDefaults.buttonColors(containerColor = fundo, contentColor = frente,
                disabledContainerColor = CINZA_LINHA, disabledContentColor = Color(0xFFA3A1A1))) {
            Text(texto, fontWeight = FontWeight.Bold, fontSize = 13.sp)
        }
    }
}
