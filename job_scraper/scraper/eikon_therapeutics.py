from job_scraper.scraper._greenhouse import scrape_board

scrape = scrape_board("eikontherapeutics", name="Eikon Therapeutics")

if __name__ == "__main__":
    from job_scraper.scraper import run

    run(scrape)
