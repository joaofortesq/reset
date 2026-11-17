package br.manutencao.reset

import android.content.Context
import org.json.JSONObject

/** Um contador de almofada: endereços da EEPROM (byte menos significativo primeiro). */
data class Contador(val nome: String, val enderecos: List<Int>, val divisor: Double?)

/** Perfil de um modelo, carregado de assets/modelos.json (mesma tabela do programa do Windows). */
data class Perfil(
    val chave: String,
    val aliases: List<String>,
    val confianca: String,
    val readKey: IntArray,
    val writeKey: String,
    val contadores: List<Contador>,
    val reset: LinkedHashMap<Int, Int>?,
    val commit: Map<Int, Int>,
)

val CONFIANCA = mapOf(
    "A" to "Validado (mesma família testada)",
    "B" to "Duas fontes concordam",
    "C" to "Uma fonte",
    "D" to "Atenção: pode ser bloqueado",
)

val NOMES_CONTADOR = mapOf(
    "principal" to "Almofada principal",
    "sem_margem" to "Almofada sem margem",
    "terceiro" to "Terceira almofada",
)

object Modelos {
    private var cache: List<Perfil>? = null

    fun carregar(ctx: Context): List<Perfil> {
        cache?.let { return it }
        val txt = ctx.assets.open("modelos.json").bufferedReader(Charsets.UTF_8).use { it.readText() }
        val raiz = JSONObject(txt)
        val lista = mutableListOf<Perfil>()
        for (chave in raiz.keys()) {
            if (chave.startsWith("_")) continue
            val m = raiz.getJSONObject(chave)
            val aliases = m.optJSONArray("aliases")?.let { a -> (0 until a.length()).map { a.getString(it) } } ?: listOf(chave)
            val rk = m.getJSONArray("read_key")
            val conts = mutableListOf<Contador>()
            val jc = m.getJSONObject("contadores")
            for (nome in jc.keys()) {
                val c = jc.getJSONObject(nome)
                val ends = c.getJSONArray("enderecos").let { a -> (0 until a.length()).map { a.getInt(it) } }
                val div = if (c.isNull("divisor")) null else c.getDouble("divisor")
                conts += Contador(nome, ends, div)
            }
            val reset = if (m.isNull("reset")) null else LinkedHashMap<Int, Int>().also { mapa ->
                val jr = m.getJSONObject("reset")
                for (k in jr.keys()) mapa[k.toInt()] = jr.getInt(k)
            }
            val commit = mutableMapOf<Int, Int>()
            m.optJSONObject("commit")?.let { jo -> for (k in jo.keys()) commit[k.toInt()] = jo.getInt(k) }
            lista += Perfil(
                chave, aliases, m.optString("confianca", "C"),
                intArrayOf(rk.getInt(0), rk.getInt(1)), m.getString("write_key"), conts, reset, commit,
            )
        }
        cache = lista
        return lista
    }

    /** Acha o perfil pelo campo MDL do Device ID (ex.: "L3150 Series"). */
    fun achar(ctx: Context, mdl: String): Perfil? {
        val nome = mdl.uppercase().replace("SERIES", "").trim()
        val todos = carregar(ctx)
        todos.firstOrNull { it.chave.uppercase() == nome }?.let { return it }
        return todos.firstOrNull { p -> p.aliases.any { it.uppercase() == nome } }
    }
}

/** "MFG:EPSON;MDL:L3150 Series;..." -> mapa */
fun parseDeviceId(s: String): Map<String, String> =
    s.split(";").mapNotNull { parte ->
        val i = parte.indexOf(':')
        if (i > 0) parte.substring(0, i).trim() to parte.substring(i + 1).trim() else null
    }.toMap()
